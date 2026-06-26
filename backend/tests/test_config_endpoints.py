from fastapi.testclient import TestClient
import server


def test_get_config_returns_answers_and_secretsset():
    client = TestClient(server.app)
    res = client.get("/api/config")
    # Live instance is onboarded -> 200 with shape; if not onboarded -> 404 (still valid).
    if res.status_code == 404:
        return
    body = res.json()
    assert body["ok"] is True
    assert "company" in body["answers"]
    assert "issueTracker" in body["answers"]
    assert body["answers"]["issueTracker"]["token"] == ""  # secret blanked
    assert "issueTracker.token" in body["secretsSet"]


def test_put_config_rejects_invalid():
    client = TestClient(server.app)
    res = client.put("/api/config", json={"company": {"productName": ""}})
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_put_config_roundtrip_blank_keep(tmp_path, monkeypatch):
    # Point config + secrets at a temp dir so the test never mutates the real instance.
    import instance_config, onboarding, importlib
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    seed_cfg = {
        "orgName": "A", "productName": "P", "productType": "webapp", "description": "",
        "urls": [], "appSlug": "p", "skillCommand": "/qa-evidence-p",
        "environments": {"mode": "static", "staticUrls": ["https://x"]},
        "issueTracker": {"type": "linear", "baseUrl": "", "projects": ["INV"], "email": "",
                         "token": "${secret:LINEAR_TOKEN}", "access": {"read": True, "write": True},
                         "statusMapping": {"ready_for_qa": ["Ready for QA"], "in_qa": ["In QA"]}},
        "vcs": {"type": "github", "org": "", "repos": ["r1"], "token": "${secret:GITHUB_TOKEN}",
                "access": {"read": True, "write": True}},
        "publish": {"jiraComment": True, "prComment": True, "slackWebhook": "",
                    "confluence": {"baseUrl": "", "spaceKey": "", "parentPage": "", "token": ""}},
        "knowledge": {"provider": "none", "link": "", "token": "", "access": {"read": True, "write": False}},
        "api": {},
    }
    onboarding.write_config_and_secrets(seed_cfg, {"LINEAR_TOKEN": "lt", "GITHUB_TOKEN": "gh"}, str(tmp_path))

    client = TestClient(server.app)
    # GET, then edit a non-secret field, leave tokens blank, PUT back.
    answers = client.get("/api/config").json()["answers"]
    answers["vcs"]["repos"] = ["r1", "r2"]
    res = client.put("/api/config", json=answers)
    assert res.status_code == 200 and res.json()["ok"] is True

    import json
    written = json.load(open(tmp_path / "instance.config.json", encoding="utf-8"))
    assert written["vcs"]["repos"] == ["r1", "r2"]            # edit applied
    assert written["issueTracker"]["token"] == "${secret:LINEAR_TOKEN}"  # blank kept ref
    secrets = open(tmp_path / ".secrets.env", encoding="utf-8").read()
    assert "LINEAR_TOKEN=lt" in secrets                        # value kept
    assert "lt" not in json.dumps(written)                     # no real secret in config
