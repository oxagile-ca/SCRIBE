from fastapi.testclient import TestClient
import server
import linear_client


def test_tickets_carry_difficulty(monkeypatch):
    async def fake_get_tickets(token, projects):
        return [{
            "key": "INV-1", "summary": "x", "status": "Ready for QA",
            "priority": "Medium", "assignee": "", "qaAssignee": "",
            "description": "AC:\n- criterion alpha line\n- criterion beta line\n- criterion gamma line\n- criterion delta line",
            "flagged": False, "staleDays": 0, "devInfo": [],
            "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
        }]
    monkeypatch.setattr(linear_client, "get_tickets", fake_get_tickets)
    monkeypatch.setattr(server, "check_evidence",
                        lambda k: {"status": "none", "score": None, "time": "", "reportPath": ""})
    client = TestClient(server.app)
    res = client.get("/api/tickets")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list) and body, f"expected non-empty list, got {body!r}"
    assert "difficulty" in body[0] and "difficultyScore" in body[0]
    assert body[0]["difficulty"] == "Medium"   # 4 ACs
