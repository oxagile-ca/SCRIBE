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
    res = client.post("/api/automation", json={"enabled": True, "armed": False})
    assert res.status_code == 200
    assert res.json()["autoMode"]["enabled"] is True
    # reset
    client.post("/api/automation", json={"enabled": False, "armed": False})


def test_qa_run_returns_stream_id(monkeypatch):
    client = TestClient(server.app)
    res = client.post("/api/qa-run/INV-1", json={"envUrl": "https://x"})
    assert res.status_code == 200
    assert "streamId" in res.json()
