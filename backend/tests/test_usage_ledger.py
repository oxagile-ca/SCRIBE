import json
import os

import usage_ledger as ul


def test_parse_result_usage_pulls_cost_and_tokens():
    event = {
        "type": "result", "subtype": "success", "total_cost_usd": 0.0123,
        "duration_ms": 4200,
        "usage": {"input_tokens": 1200, "output_tokens": 340,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 7},
    }
    u = ul.parse_result_usage(event)
    assert u["cost_usd"] == 0.0123
    assert u["input_tokens"] == 1200
    assert u["output_tokens"] == 340
    assert u["cache_read_input_tokens"] == 7
    assert u["duration_ms"] == 4200


def test_parse_result_usage_defaults_to_zero_when_missing():
    u = ul.parse_result_usage({"type": "result"})
    assert u == {"cost_usd": 0.0, "duration_ms": 0, "input_tokens": 0,
                 "output_tokens": 0, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}


def test_parse_model_from_init():
    assert ul.parse_model_from_init(
        {"type": "system", "subtype": "init", "model": "claude-haiku-4-5"}
    ) == "claude-haiku-4-5"
    assert ul.parse_model_from_init({"type": "assistant"}) is None


def test_record_then_aggregate_groups_by_task_and_model(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    ul.record(task="qa-evidence", ticket="INV-1", pipeline_id="pl1",
              model="claude-haiku-4-5",
              usage={"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 20},
              path=p)
    ul.record(task="code-reviewer", ticket="INV-1", pipeline_id="pl1",
              model="default",
              usage={"cost_usd": 0.50, "input_tokens": 8000, "output_tokens": 900},
              path=p)
    ul.record(task="qa-evidence", ticket="INV-2", pipeline_id="pl2",
              model="claude-haiku-4-5",
              usage={"cost_usd": 0.02, "input_tokens": 200, "output_tokens": 30},
              path=p)

    agg = ul.aggregate_for_ticket("INV-1", path=p)
    assert agg["cost_usd"] == 0.51
    assert agg["input_tokens"] == 8100
    assert agg["output_tokens"] == 920
    tasks = {(t["task"], t["model"]): t for t in agg["tasks"]}
    assert tasks[("qa-evidence", "claude-haiku-4-5")]["cost_usd"] == 0.01
    assert tasks[("code-reviewer", "default")]["input_tokens"] == 8000


def test_aggregate_missing_file_is_empty(tmp_path):
    agg = ul.aggregate_for_ticket("NOPE", path=str(tmp_path / "absent.jsonl"))
    assert agg == {"tasks": [], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def test_record_writes_one_json_line(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    rec = ul.record(task="chat", ticket=None, pipeline_id=None, model=None,
                    usage={"cost_usd": 0.005, "input_tokens": 50, "output_tokens": 5},
                    session_id="s1", path=p)
    assert rec["model"] == "default"   # None coerced to "default"
    assert rec["ticket"] is None
    with open(p, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["task"] == "chat"


def test_summary_today_equals_alltime_when_all_today(tmp_path, monkeypatch):
    p = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(ul, "_now_iso", lambda: "2026-06-20T10:00:00Z")
    ul.record(task="chat", ticket=None, pipeline_id=None, model="default",
              usage={"cost_usd": 0.03, "input_tokens": 300, "output_tokens": 40},
              path=p)
    s = ul.summary(path=p)
    assert s["allTime"]["cost_usd"] == 0.03
    assert s["today"]["cost_usd"] == 0.03
    assert s["allTime"]["input_tokens"] == 300
