from pathlib import Path

from pipeline_store import PipelineStore


def test_set_and_get_meta_roundtrips_json(tmp_path: Path):
    store = PipelineStore(str(tmp_path / "test.db"))
    store.set_meta("prev_ready_set", ["PROJ-100", "PROJ-101"])
    assert store.get_meta("prev_ready_set") == ["PROJ-100", "PROJ-101"]


def test_get_meta_returns_default_when_missing(tmp_path: Path):
    store = PipelineStore(str(tmp_path / "test.db"))
    assert store.get_meta("never_set", default=[]) == []
    assert store.get_meta("never_set") is None


def test_set_meta_overwrites(tmp_path: Path):
    store = PipelineStore(str(tmp_path / "test.db"))
    store.set_meta("k", "first")
    store.set_meta("k", "second")
    assert store.get_meta("k") == "second"


def test_meta_persists_across_store_reopen(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    store = PipelineStore(db_path)
    store.set_meta("k", ["a", "b"])
    store._conn.close()

    store2 = PipelineStore(db_path)
    assert store2.get_meta("k") == ["a", "b"]
