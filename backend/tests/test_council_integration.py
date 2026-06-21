import os
import json

import pytest
from fastapi.testclient import TestClient
from council import _run_reviewer, Reviewer
from council_prompts import build_qa_evidence_prompt


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.asyncio
async def test_run_reviewer_captures_usage_and_model(monkeypatch):
    # NOTE: spawns a subprocess; errors with OSError WinError 193/6 on Windows (pre-existing limitation — CI/Linux-verified).
    stub = os.path.join(FIXTURES_DIR, "stub_claude_pass_with_usage.sh")
    monkeypatch.setenv("CLAUDE_BIN", stub)
    rv = Reviewer(name="qa-evidence", prompt_builder=build_qa_evidence_prompt)
    outcome = await _run_reviewer(rv, {"ticket_key": "INV-1", "run_name": "r1"})
    assert outcome["verdict"] == "PASS"
    assert outcome["model"] == "claude-haiku-4-5"
    assert outcome["usage"]["cost_usd"] == 0.0123
    assert outcome["usage"]["input_tokens"] == 1200


def _set_stub(monkeypatch, name):
    path = os.path.join(FIXTURES_DIR, f"stub_claude_{name}.sh")
    monkeypatch.setenv("CLAUDE_BIN", path)


def _seed_evidence(tmp_path, monkeypatch, ticket_key="PROJ-COUNCIL"):
    """Lay down a minimal evidence run with summary.json so check_evidence finds it.

    `EVIDENCE_DIR` is captured at import time inside `config` and re-exported
    from `agents`, so we monkeypatch the live binding in `agents` directly.
    """
    ev_root = tmp_path / "evidence" / ticket_key / "runs" / "2026-06-07_15-32-11"
    (ev_root / "automated").mkdir(parents=True, exist_ok=True)
    (ev_root / "automated" / "trace.zip").write_bytes(b"fake trace")
    (ev_root / "summary.json").write_text(json.dumps({"score": 95}))
    import agents
    monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path / "evidence"))
    return str(ev_root)


@pytest.mark.asyncio
async def test_check_evidence_starts_council_on_pass(tmp_path, monkeypatch):
    _set_stub(monkeypatch, "pass")
    _seed_evidence(tmp_path, monkeypatch)

    # Stub _gather_pr_refs so the test doesn't hit Jira.
    import server
    async def _fake_gather(_key):
        return []
    monkeypatch.setattr(server, "_gather_pr_refs", _fake_gather)

    # Seed a pipeline state for the ticket so the council has a lane to attach to.
    server.pipeline_states["pipe-A"] = {"ticketKey": "PROJ-COUNCIL", "stage": "inspector"}
    try:
        client = TestClient(server.app)
        resp = client.post("/api/check-evidence/PROJ-COUNCIL", json={"baseline_runs": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("found") is True
        assert body.get("awaitingCouncil") is True
        assert "councilStreamId" in body
    finally:
        server.pipeline_states.pop("pipe-A", None)


def test_override_blocks_then_succeeds():
    from server import app, pipeline_states, pipeline_store
    pipeline_states["pipe-B"] = {"ticketKey": "PROJ-OVR", "stage": "inspector", "councilStatus": "block", "councilPayload": {"verdict": "BLOCK"}}
    pipeline_store.upsert("pipe-B", pipeline_states["pipe-B"])
    try:
        client = TestClient(app)
        resp = client.post("/api/council/override/pipe-B", json={"reason": "  "})
        assert resp.status_code == 400
        resp = client.post("/api/council/override/pipe-B", json={"reason": "flake, see slack"})
        assert resp.status_code == 200
        state = pipeline_store.get("pipe-B")
        assert state["councilStatus"] == "overridden"
        assert state["councilOverride"]["reason"] == "flake, see slack"
    finally:
        pipeline_states.pop("pipe-B", None)


def test_override_rejected_when_not_blocked():
    from server import app, pipeline_states, pipeline_store
    pipeline_states["pipe-C"] = {"ticketKey": "PROJ-PASS", "stage": "inspector", "councilStatus": "pass"}
    pipeline_store.upsert("pipe-C", pipeline_states["pipe-C"])
    try:
        client = TestClient(app)
        resp = client.post("/api/council/override/pipe-C", json={"reason": "trying to override a PASS"})
        assert resp.status_code == 400
    finally:
        pipeline_states.pop("pipe-C", None)


def test_get_council_returns_persisted_verdict():
    from server import app, pipeline_states, pipeline_store
    payload = {"verdict": "PASS", "reviewers": [{"name": "qa-evidence", "verdict": "PASS"}]}
    pipeline_states["pipe-D"] = {"ticketKey": "PROJ-GET", "councilStatus": "pass", "councilPayload": payload}
    pipeline_store.upsert("pipe-D", pipeline_states["pipe-D"])
    try:
        client = TestClient(app)
        resp = client.get("/api/council/pipe-D")
        assert resp.status_code == 200
        body = resp.json()
        assert body["councilStatus"] == "pass"
        assert body["councilPayload"] == payload
    finally:
        pipeline_states.pop("pipe-D", None)


@pytest.mark.asyncio
async def test_pending_council_restarts_on_pipeline_resume(tmp_path, monkeypatch):
    _set_stub(monkeypatch, "pass")
    _seed_evidence(tmp_path, monkeypatch)
    from server import app, pipeline_states, pipeline_store

    async def _fake_gather(_key):
        return []
    import server as server_mod
    monkeypatch.setattr(server_mod, "_gather_pr_refs", _fake_gather)

    pipeline_states["pipe-resume"] = {
        "ticketKey": "PROJ-COUNCIL",
        "stage": "inspector",
        "councilStatus": "pending",
    }
    pipeline_store.upsert("pipe-resume", pipeline_states["pipe-resume"])

    try:
        client = TestClient(app)
        resp = client.post("/api/pipeline/resume/pipe-resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("resumedCouncil") is True
        assert "councilStreamId" in body
    finally:
        pipeline_states.pop("pipe-resume", None)
