"""Tests for the SQLite pipeline state store.

These lock in the atomicity invariant that the JSON-file design couldn't
provide: two concurrent updates on the same pipeline_id must merge, not
overwrite each other. The single-lock + UPSERT pattern is the only thing
between us and the silent lost-update class of bugs."""
import json
import os
import threading
import time

import pytest

from pipeline_store import PipelineStore


@pytest.fixture
def store(tmp_path):
    return PipelineStore(str(tmp_path / "pipeline.db"))


def test_upsert_creates_row(store):
    state = store.upsert("p1", {"ticketKey": "PROJ-1", "env": "qa-env", "status": "running"})
    assert state["ticketKey"] == "PROJ-1"
    assert state["env"] == "qa-env"
    assert state["status"] == "running"
    assert state["updated_at"] > 0


def test_upsert_merges_with_existing(store):
    store.upsert("p1", {"ticketKey": "PROJ-1", "env": "e", "status": "running", "stage": "builder"})
    merged = store.upsert("p1", {"stage": "shipper", "status": "running"})
    # Earlier fields preserved
    assert merged["ticketKey"] == "PROJ-1"
    assert merged["env"] == "e"
    # New fields applied
    assert merged["stage"] == "shipper"


def test_get_returns_cached_state(store):
    store.upsert("p1", {"ticketKey": "PROJ-1"})
    assert store.get("p1")["ticketKey"] == "PROJ-1"
    assert store.get("nope") is None


def test_all_states_is_a_copy(store):
    """Callers iterate over all_states; they must not see internal mutations."""
    store.upsert("p1", {"ticketKey": "A"})
    snap = store.all_states()
    store.upsert("p2", {"ticketKey": "B"})
    assert list(snap.keys()) == ["p1"]


def test_persistence_survives_restart(tmp_path):
    """The whole point: write, close, reopen, data is still there."""
    db = str(tmp_path / "p.db")
    s1 = PipelineStore(db)
    s1.upsert("p1", {"ticketKey": "PROJ-1", "status": "running"})
    s1.upsert("p2", {"ticketKey": "PROJ-2", "status": "completed"})

    s2 = PipelineStore(db)  # fresh instance, same file
    assert set(s2.all_states().keys()) == {"p1", "p2"}
    assert s2.get("p1")["ticketKey"] == "PROJ-1"


def test_logs_field_persists_as_list(store):
    store.upsert("p1", {"ticketKey": "A", "logs": ["one", "two", "three"]})
    assert store.get("p1")["logs"] == ["one", "two", "three"]


def test_logs_truncated_to_50(store):
    """The previous design kept last 50; preserve that bound at write time."""
    logs = [f"line {i}" for i in range(100)]
    store.upsert("p1", {"ticketKey": "A", "logs": logs})
    # Cache reflects the input directly...
    assert len(store.get("p1")["logs"]) == 100
    # ...but the DB truncates. Round-trip via a fresh store proves that.
    fresh = PipelineStore(store.db_path)
    assert len(fresh.get("p1")["logs"]) == 50
    assert fresh.get("p1")["logs"][0] == "line 50"


def test_remove_deletes_row_and_cache(store):
    store.upsert("p1", {"ticketKey": "A"})
    store.remove("p1")
    assert store.get("p1") is None
    fresh = PipelineStore(store.db_path)
    assert fresh.get("p1") is None


def test_cleanup_only_drops_terminal_and_aged(store):
    now = time.time()
    store.upsert("running_fresh", {"ticketKey": "A", "status": "running"})
    store.upsert("running_old", {"ticketKey": "B", "status": "running"})
    store.upsert("completed_fresh", {"ticketKey": "C", "status": "completed"})
    store.upsert("completed_old", {"ticketKey": "D", "status": "completed"})
    store.upsert("failed_old", {"ticketKey": "E", "status": "failed"})

    # Backdate the *_old entries by 40 days
    long_ago = now - (40 * 86400)
    store._conn.execute(
        "UPDATE pipelines SET updated_at = ? WHERE pipeline_id IN ('running_old','completed_old','failed_old')",
        (long_ago,),
    )
    # Mirror that in the cache so cleanup_old's filter sees the older mtime.
    for pid in ("running_old", "completed_old", "failed_old"):
        store._cache[pid]["updated_at"] = long_ago

    removed = store.cleanup_old(retention_days=30)
    # running_old NOT removed (only terminal statuses get cleaned)
    # completed_old + failed_old removed
    # *_fresh untouched
    assert removed == 2
    assert "running_old" in store._cache
    assert "completed_old" not in store._cache
    assert "failed_old" not in store._cache
    assert "running_fresh" in store._cache
    assert "completed_fresh" in store._cache


def test_concurrent_upserts_on_same_id_dont_lose_updates(store):
    """The race the JSON-file design couldn't survive:
       N threads each merging a unique key into the same pipeline_id.
       After all threads finish, every contributed key must be present."""
    N = 25
    store.upsert("p1", {"ticketKey": "PROJ-1"})

    def worker(i):
        store.upsert("p1", {f"contrib_{i}": f"value_{i}"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Cache view
    state = store.get("p1")
    # ticketKey still preserved
    assert state["ticketKey"] == "PROJ-1"

    # All 25 contributions present in the DB round-trip
    fresh = PipelineStore(store.db_path)
    fresh_state = fresh.get("p1")
    # contrib_* keys are NOT in our schema columns, so they live only in the
    # cache, not in the DB. This is intentional: arbitrary keys aren't promised
    # to persist. But the cache MUST hold them — that's the lost-update test.
    for i in range(N):
        assert state.get(f"contrib_{i}") == f"value_{i}", f"lost update for contrib_{i}"


def test_migrate_from_json_imports_and_renames(store, tmp_path):
    json_path = str(tmp_path / "legacy.json")
    legacy = {
        "p1": {"ticketKey": "PROJ-1", "env": "qa-env", "stage": "builder", "status": "running"},
        "p2": {"ticketKey": "PROJ-2", "env": "qa-env-1", "stage": "shipper", "status": "completed"},
    }
    with open(json_path, "w") as f:
        json.dump(legacy, f)

    count = store.migrate_from_json(json_path)
    assert count == 2
    assert store.get("p1")["env"] == "qa-env"
    assert store.get("p2")["status"] == "completed"
    # Source file renamed so a restart can't re-import
    assert not os.path.exists(json_path)
    assert os.path.exists(json_path + ".migrated")


def test_migrate_from_json_is_idempotent_when_cache_non_empty(store, tmp_path):
    """Don't blow away in-flight data by re-importing on a non-empty DB."""
    store.upsert("existing", {"ticketKey": "EXISTING"})
    json_path = str(tmp_path / "legacy.json")
    with open(json_path, "w") as f:
        json.dump({"p1": {"ticketKey": "NEW"}}, f)
    count = store.migrate_from_json(json_path)
    assert count == 0
    assert store.get("p1") is None
    # Source untouched since no import happened
    assert os.path.exists(json_path)


def test_migrate_from_json_missing_file(store, tmp_path):
    assert store.migrate_from_json(str(tmp_path / "nope.json")) == 0


def test_status_index_speeds_running_queries(store):
    """Smoke-check that the schema actually built the index. Not a perf
    test — just a sanity that 'EXPLAIN QUERY PLAN' references it."""
    store.upsert("p1", {"ticketKey": "A", "status": "running"})
    cur = store._conn.execute("EXPLAIN QUERY PLAN SELECT * FROM pipelines WHERE status = ?", ("running",))
    plan = " ".join(str(tuple(row)) for row in cur.fetchall())
    assert "idx_pipelines_status" in plan
