import os
import tempfile
import pytest

from pipeline_store import PipelineStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "pipeline-state.db"
    return PipelineStore(str(db))


def test_council_columns_default_null(store):
    state = store.upsert("pipe1", {"ticketKey": "PROJ-1"})
    assert state.get("councilStatus") is None
    assert state.get("councilPayload") is None
    assert state.get("councilOverride") is None


def test_can_write_council_status(store):
    store.upsert("pipe1", {"ticketKey": "PROJ-1"})
    state = store.upsert("pipe1", {"councilStatus": "pending"})
    assert state["councilStatus"] == "pending"
    # Persistence round-trip: must survive restart via the new SQLite column.
    fresh = PipelineStore(store.db_path)
    assert fresh.get("pipe1")["councilStatus"] == "pending"


def test_can_write_council_payload(store):
    store.upsert("pipe1", {"ticketKey": "PROJ-1"})
    payload = {"verdict": "BLOCK", "reviewers": [{"name": "qa-evidence", "verdict": "PASS"}]}
    state = store.upsert("pipe1", {"councilStatus": "block", "councilPayload": payload})
    assert state["councilPayload"] == payload
    # Persistence round-trip: dict payload must survive JSON-encode → SQLite → JSON-decode.
    fresh = PipelineStore(store.db_path)
    assert fresh.get("pipe1")["councilPayload"] == payload
    assert fresh.get("pipe1")["councilStatus"] == "block"


def test_existing_rows_migrate_with_null_council(tmp_path):
    db_path = str(tmp_path / "pipeline-state.db")
    store_v1 = PipelineStore(db_path)
    store_v1.upsert("legacy1", {"ticketKey": "PROJ-OLD"})
    del store_v1
    store_v2 = PipelineStore(db_path)
    state = store_v2.get("legacy1")
    assert state is not None
    assert state.get("councilStatus") is None


def test_legacy_db_without_council_cols_migrates(tmp_path):
    """True legacy-schema migration: build a pre-council DB by hand and confirm
    the ALTER TABLE branch in _init_schema() actually adds the new columns.

    The sibling test above can't exercise this branch because PipelineStore's
    CREATE TABLE IF NOT EXISTS already includes all council columns — the
    PRAGMA set-difference comes back empty and no ALTER fires. Here we bypass
    PipelineStore entirely for the initial schema so the migration has real
    work to do.
    """
    import sqlite3
    db_path = str(tmp_path / "p.db")
    raw = sqlite3.connect(db_path)
    raw.execute("""CREATE TABLE pipelines (
        pipeline_id TEXT PRIMARY KEY,
        ticket_key TEXT NOT NULL DEFAULT '',
        env TEXT NOT NULL DEFAULT '',
        repo TEXT NOT NULL DEFAULT '',
        branch TEXT NOT NULL DEFAULT '',
        service TEXT NOT NULL DEFAULT '',
        snapshot TEXT NOT NULL DEFAULT '',
        env_url TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT 'builder',
        status TEXT NOT NULL DEFAULT 'running',
        logs_json TEXT NOT NULL DEFAULT '[]',
        updated_at REAL NOT NULL
    )""")
    raw.execute(
        "INSERT INTO pipelines (pipeline_id, ticket_key, updated_at) VALUES (?, ?, ?)",
        ("legacy1", "PROJ-OLD", 12345.0),
    )
    raw.commit()
    raw.close()

    store = PipelineStore(db_path)
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(pipelines)").fetchall()}
    assert {"council_status", "council_payload", "council_override"}.issubset(cols)
    legacy = store.get("legacy1")
    assert legacy is not None
    assert legacy["councilStatus"] is None
    assert legacy["councilPayload"] is None
    assert legacy["councilOverride"] is None
