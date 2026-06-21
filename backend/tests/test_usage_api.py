# backend/tests/test_usage_api.py
import usage_ledger
from fastapi.testclient import TestClient
import server


def test_usage_ticket_combines_ledger_and_otel(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    # No OTEL cost for this ticket in the test env.
    monkeypatch.setattr(server._otel, "total_cost_for_ticket", lambda *_a, **_k: None)
    usage_ledger.record(task="code-reviewer", ticket="INV-9", pipeline_id="p",
                        model="default",
                        usage={"cost_usd": 0.5, "input_tokens": 8000, "output_tokens": 900},
                        path=ledger)

    client = TestClient(server.app)
    r = client.get("/api/usage/ticket/INV-9")
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"] == "INV-9"
    assert body["total_cost_usd"] == 0.5
    assert body["total_input_tokens"] == 8000
    assert any(t["task"] == "code-reviewer" for t in body["tasks"])


def test_usage_ticket_appends_evidence_runs_row(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    monkeypatch.setattr(server._otel, "total_cost_for_ticket", lambda *_a, **_k: 1.25)
    client = TestClient(server.app)
    body = client.get("/api/usage/ticket/INV-X").json()
    ev = [t for t in body["tasks"] if t["task"] == "evidence-runs"][0]
    assert ev["cost_usd"] == 1.25
    assert ev["input_tokens"] is None
    assert body["total_cost_usd"] == 1.25


def test_usage_summary_shape(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    client = TestClient(server.app)
    body = client.get("/api/usage/summary").json()
    assert set(body.keys()) == {"today", "allTime"}
    assert "cost_usd" in body["allTime"]
