from fastapi.testclient import TestClient
import server


def test_automation_get_returns_shape():
    client = TestClient(server.app)
    res = client.get("/api/automation")
    assert res.status_code == 200
    body = res.json()
    assert "writeAllowed" in body
    assert "autoMode" in body and "enabled" in body["autoMode"] and "armed" in body["autoMode"]


def test_automation_post_sets_state():
    client = TestClient(server.app)
    try:
        res = client.post("/api/automation", json={"enabled": True, "armed": False})
        assert res.status_code == 200
        assert res.json()["autoMode"]["enabled"] is True
    finally:
        client.post("/api/automation", json={"enabled": False, "armed": False})


def test_qa_run_returns_stream_id(monkeypatch):
    import qa_orchestrator
    async def _fake_finalize(*args, **kwargs):
        yield {"type": "done", "success": True, "report_url": "", "pdf": None,
               "attached": False, "skipped_reason": None, "error": None}
    monkeypatch.setattr(qa_orchestrator, "run_and_finalize", _fake_finalize)
    client = TestClient(server.app)
    res = client.post("/api/qa-run/INV-1", json={"envUrl": "https://x"})
    assert res.status_code == 200
    assert "streamId" in res.json()


def test_qa_run_rejects_bad_key():
    client = TestClient(server.app)
    res = client.post("/api/qa-run/not a key", json={"envUrl": "x"})
    assert res.status_code == 400
