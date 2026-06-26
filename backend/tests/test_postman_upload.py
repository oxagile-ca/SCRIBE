import json
import onboarding


def _collection():
    return json.dumps({
        "info": {"name": "API"},
        "item": [
            {"name": "Users", "item": [
                {"name": "list", "request": {"method": "GET", "url": {"raw": "{{BASE}}/users"}}},
                {"name": "create", "request": {"method": "POST", "url": {"raw": "{{BASE}}/users"}}},
            ]},
        ],
    }).encode("utf-8")


def test_save_postman_sets_path_and_counts(tmp_path):
    cfg = {"appSlug": "beeventory", "api": {"baseUrl": "https://api"}}
    cfg2, count = onboarding.save_postman_collection(_collection(), cfg, str(tmp_path))
    assert count == 2
    assert cfg2["api"]["postmanCollectionPath"].endswith("beeventory.postman_collection.json")
    assert (tmp_path / "beeventory.postman_collection.json").exists()


def test_save_postman_rejects_non_json(tmp_path):
    cfg = {"appSlug": "x", "api": {}}
    import pytest
    with pytest.raises(ValueError):
        onboarding.save_postman_collection(b"not json {", cfg, str(tmp_path))


def test_upload_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server, onboarding
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    onboarding.write_config_and_secrets(
        {"appSlug": "beeventory", "productName": "B", "api": {}}, {}, str(tmp_path))
    client = TestClient(server.app)
    res = client.post("/api/config/upload-postman",
                      files={"file": ("c.json", _collection(), "application/json")})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["endpointCount"] == 2
    assert body["path"]
    assert body["path"].endswith("beeventory.postman_collection.json")


def test_upload_rejects_invalid_json(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server, onboarding
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    onboarding.write_config_and_secrets(
        {"appSlug": "beeventory", "productName": "B", "api": {}}, {}, str(tmp_path))
    client = TestClient(server.app)
    res = client.post("/api/config/upload-postman",
                      files={"file": ("bad.json", b"not json {", "application/json")})
    assert res.status_code == 400
    body = res.json()
    assert body["ok"] is False


def test_upload_404_when_not_onboarded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    client = TestClient(server.app)
    res = client.post("/api/config/upload-postman",
                      files={"file": ("c.json", _collection(), "application/json")})
    assert res.status_code == 404
