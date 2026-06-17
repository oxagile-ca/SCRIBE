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
os.environ["SCRIBE_SKILL_DIR"] = os.path.join(_TMP, "skills")
os.environ["SCRIBE_INSTANCES_DIR"] = os.path.join(_TMP, "instances")

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import linear_client  # noqa: E402

client = TestClient(server.app)


def _write_config(cfg):
    with open(os.environ["SCRIBE_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


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


def test_projects_reflect_onboarded_config_live():
    _write_config({
        "productName": "Beeventory",
        "issueTracker": {"type": "linear", "projects": ["BEE"]},
        "vcs": {"type": "github"},
        "environments": {"mode": "static", "staticUrls": ["http://x"]},
    })
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == {"projects": ["BEE"], "default": "BEE"}


def test_tickets_dispatch_to_linear_when_configured(monkeypatch):
    _write_config({
        "productName": "Beeventory",
        "issueTracker": {"type": "linear", "projects": ["BEE"]},
        "vcs": {"type": "github"},
        "environments": {"mode": "static", "staticUrls": ["http://x"]},
    })

    async def fake_get_tickets(api_key, team_keys=None):
        assert team_keys == ["BEE"]
        return [{
            "key": "BEE-12", "summary": "Add SKU search", "status": "Ready for testing",
            "priority": "High", "assignee": "J. Doe", "qaAssignee": "",
            "description": "", "flagged": False, "staleDays": 0, "devInfo": [],
            "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
        }]

    monkeypatch.setattr(linear_client, "get_tickets", fake_get_tickets)

    r = client.get("/api/tickets?project=BEE")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    assert data[0]["key"] == "BEE-12"
    assert "evidence" in data[0]  # enriched server-side
    # Linear's "Ready for testing" normalizes to the canonical ready_for_qa category
    assert data[0]["statusCategory"] == "ready_for_qa"
