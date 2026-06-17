"""Endpoint tests for the onboarding API. Redirects all output + state to tmp via env
so the test never touches the real home dir."""
import os
import tempfile

# Redirect app state (streams/db) and onboarding output to a temp dir BEFORE importing
# server, which creates those at import time.
_TMP = tempfile.mkdtemp(prefix="scribe-test-")
os.environ["HOME"] = _TMP
os.environ["USERPROFILE"] = _TMP
os.environ["SCRIBE_CONFIG"] = os.path.join(_TMP, "instance.config.json")
os.environ["SCRIBE_CONFIG_DIR"] = _TMP
os.environ["SCRIBE_SKILL_DIR"] = os.path.join(_TMP, "skill")

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)


def _valid_answers():
    return {
        "company": {"productName": "Acme CMS", "productType": "cms", "urls": []},
        "environments": {"mode": "static", "staticUrls": ["https://staging.acme.example.com"]},
        "issueTracker": {"type": "jira", "baseUrl": "https://acme.atlassian.net",
                         "projects": ["ACME"], "token": "jira-tok",
                         "access": {"read": True, "write": True}},
        "vcs": {"type": "github", "org": "acme", "repos": ["acme/cms"], "token": "gh-tok",
                "access": {"read": True, "write": False}},
        "publish": {"jiraComment": True},
        "productQA": {"criticalFlows": ["Publish an article"], "riskAreas": [], "alwaysCheck": []},
        "knowledge": {"provider": "none"},
        "anthropicKey": "sk-ant-test",
    }


def test_status_is_unconfigured_then_configured_after_submit():
    r = client.get("/api/onboarding/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False

    r2 = client.post("/api/onboarding", json=_valid_answers())
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True
    assert r2.json()["summary"]["productName"] == "Acme CMS"

    r3 = client.get("/api/onboarding/status")
    assert r3.json()["configured"] is True
    assert r3.json()["productName"] == "Acme CMS"


def test_onboarding_rejects_invalid_payload_with_400():
    r = client.post("/api/onboarding", json={"company": {}})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["errors"]
