"""Tests for test_cases_store — SCRIBE-local, per-ticket user-added test cases."""
import test_cases_store as tcs


def _p(tmp_path):
    return str(tmp_path / "test-cases.json")


def test_add_and_list(tmp_path):
    p = _p(tmp_path)
    c = tcs.add_case("NOR-8", "Edit address with accented city", path=p)
    assert c["id"] and c["text"] == "Edit address with accented city" and c["ts"]
    cases = tcs.list_cases("NOR-8", path=p)
    assert [x["text"] for x in cases] == ["Edit address with accented city"]


def test_add_blank_is_ignored(tmp_path):
    p = _p(tmp_path)
    assert tcs.add_case("NOR-8", "   ", path=p) is None
    assert tcs.list_cases("NOR-8", path=p) == []


def test_delete(tmp_path):
    p = _p(tmp_path)
    a = tcs.add_case("NOR-8", "one", path=p)
    tcs.add_case("NOR-8", "two", path=p)
    assert tcs.delete_case("NOR-8", a["id"], path=p) is True
    assert [x["text"] for x in tcs.list_cases("NOR-8", path=p)] == ["two"]
    assert tcs.delete_case("NOR-8", "nope", path=p) is False


def test_keys_are_isolated(tmp_path):
    p = _p(tmp_path)
    tcs.add_case("NOR-8", "a", path=p)
    tcs.add_case("NOR-11", "b", path=p)
    assert tcs.texts_for("NOR-8", path=p) == ["a"]
    assert tcs.texts_for("NOR-11", path=p) == ["b"]


def test_persists_to_disk(tmp_path):
    p = _p(tmp_path)
    tcs.add_case("NOR-8", "persisted", path=p)
    # a fresh read (no in-memory state) sees it
    assert tcs.texts_for("NOR-8", path=p) == ["persisted"]


def test_missing_file_and_garbage_are_safe(tmp_path):
    assert tcs.list_cases("X", path=str(tmp_path / "none.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert tcs.list_cases("X", path=str(bad)) == []
