"""Endpoint tests for the local test-case store routes."""
from fastapi.testclient import TestClient
import pytest

import server
import test_cases_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the store at a temp file so tests never touch the real
    # ~/qa-dashboard/test-cases.json. STORE_PATH is read at call time.
    monkeypatch.setattr(test_cases_store, "STORE_PATH", str(tmp_path / "test-cases.json"))
    return TestClient(server.app)


def _add(client, key="NOR-8", text="original"):
    res = client.post(f"/api/test-cases/{key}", json={"text": text})
    assert res.status_code == 200
    return res.json()["case"]


def test_patch_updates_text(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={"text": "edited"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["case"]["text"] == "edited"
    assert body["case"]["id"] == case["id"]
    # and the change is visible through the list route
    listed = client.get("/api/test-cases/NOR-8").json()["cases"]
    assert [c["text"] for c in listed] == ["edited"]


def test_patch_blank_text_is_400(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={"text": "   "})
    assert res.status_code == 400
    assert res.json()["ok"] is False
    assert client.get("/api/test-cases/NOR-8").json()["cases"][0]["text"] == "original"


def test_patch_unknown_id_is_404(client):
    _add(client)
    res = client.patch("/api/test-cases/NOR-8/does-not-exist", json={"text": "edited"})
    assert res.status_code == 404
    assert res.json()["ok"] is False


def test_patch_missing_body_key_is_400(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={})
    assert res.status_code == 400
