"""Tests for the Jira dev-info parser. Locks in the field shape the
agent pipeline depends on (repo, branch, destBranch, prStatus)."""
from unittest.mock import patch, AsyncMock

import pytest

from jira_client import _get_dev_info


def _mock_response(status_code, json_payload):
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: json_payload
    return resp


def _mock_client(response):
    """Build a context-manager mock for httpx.AsyncClient(...)."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_extracts_repo_branch_destination_state():
    payload = {
        "detail": [{
            "pullRequests": [
                {
                    "id": "762",
                    "repositoryName": "acme/service-cms",
                    "status": "OPEN",
                    "source": {"branch": "autoresolve/PROJ-333"},
                    "destination": {"branch": "main"},
                },
                {
                    "id": "794",
                    "repositoryName": "acme/service-cms",
                    "status": "OPEN",
                    "source": {"branch": "qa/PROJ-333-e2e"},
                    "destination": {"branch": "autoresolve/PROJ-333"},
                },
                {
                    "id": "530",
                    "repositoryName": "acme/service-cms",
                    "status": "DECLINED",
                    "source": {"branch": "feature/PROJB-1668"},
                    "destination": {"branch": "main"},
                },
            ]
        }]
    }

    with patch("jira_client.httpx.AsyncClient") as cm:
        cm.return_value = _mock_client(_mock_response(200, payload))
        result = await _get_dev_info("123456")

    assert len(result) == 3

    by_branch = {r["branch"]: r for r in result}
    assert by_branch["autoresolve/PROJ-333"]["destBranch"] == "main"
    assert by_branch["autoresolve/PROJ-333"]["prStatus"] == "OPEN"
    assert by_branch["qa/PROJ-333-e2e"]["destBranch"] == "autoresolve/PROJ-333"
    assert by_branch["feature/PROJB-1668"]["prStatus"] == "DECLINED"


@pytest.mark.asyncio
async def test_uppercases_status():
    """The pipeline filters DECLINED by uppercase compare — make sure we
    normalize at the parser so callers don't have to."""
    payload = {
        "detail": [{
            "pullRequests": [{
                "id": "1",
                "repositoryName": "x/y",
                "status": "declined",
                "source": {"branch": "b"},
                "destination": {"branch": "main"},
            }]
        }]
    }
    with patch("jira_client.httpx.AsyncClient") as cm:
        cm.return_value = _mock_client(_mock_response(200, payload))
        result = await _get_dev_info("1")
    assert result[0]["prStatus"] == "DECLINED"


@pytest.mark.asyncio
async def test_missing_destination_does_not_crash():
    """Bitbucket returns no `destination` for some legacy PRs. Don't blow up."""
    payload = {
        "detail": [{
            "pullRequests": [{
                "id": "1",
                "repositoryName": "x/y",
                "status": "OPEN",
                "source": {"branch": "b"},
            }]
        }]
    }
    with patch("jira_client.httpx.AsyncClient") as cm:
        cm.return_value = _mock_client(_mock_response(200, payload))
        result = await _get_dev_info("1")
    assert result[0]["destBranch"] == ""


@pytest.mark.asyncio
async def test_skips_pr_with_no_branch_or_repo():
    payload = {
        "detail": [{
            "pullRequests": [
                {"id": "1", "repositoryName": "", "status": "OPEN",
                 "source": {"branch": "b"}, "destination": {"branch": "main"}},
                {"id": "2", "repositoryName": "x/y", "status": "OPEN",
                 "source": {"branch": ""}, "destination": {"branch": "main"}},
            ]
        }]
    }
    with patch("jira_client.httpx.AsyncClient") as cm:
        cm.return_value = _mock_client(_mock_response(200, payload))
        result = await _get_dev_info("1")
    assert result == []


@pytest.mark.asyncio
async def test_non_200_returns_empty():
    with patch("jira_client.httpx.AsyncClient") as cm:
        cm.return_value = _mock_client(_mock_response(401, {}))
        result = await _get_dev_info("1")
    assert result == []


# --- token load-order regression (Jira board empty on fresh installs) --------
# Bug: server.py imports config/jira_client (freezing JIRA_TOKEN from the env)
# BEFORE load_secrets_env() populates the env, so the onboarded token never
# reached _auth() and Jira was queried anonymously -> 200 with zero issues ->
# blank board. Linear was immune because it reads its token live per request.
# The fix: _auth() reads JIRA_TOKEN/JIRA_EMAIL live from os.environ, falling back
# to the frozen module constant, then ~/.claude/mcp.json.
import os
import jira_client


def test_auth_reads_live_env_token_when_module_constant_is_empty(monkeypatch):
    # Simulate the frozen-at-import empty constant...
    monkeypatch.setattr(jira_client, "JIRA_TOKEN", "")
    monkeypatch.setattr(jira_client, "JIRA_EMAIL", "cfg@example.com")
    # ...while load_secrets_env() has since populated the real token live.
    monkeypatch.setenv("JIRA_TOKEN", "live-secret-token")
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    # No mcp.json fallback in play.
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    email, token = jira_client._auth()
    assert token == "live-secret-token"          # the live env token, not ""
    assert email == "cfg@example.com"             # falls back to the config constant


def test_auth_prefers_env_email_when_present(monkeypatch):
    monkeypatch.setattr(jira_client, "JIRA_TOKEN", "")
    monkeypatch.setattr(jira_client, "JIRA_EMAIL", "cfg@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "t")
    monkeypatch.setenv("JIRA_EMAIL", "env@example.com")
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    email, token = jira_client._auth()
    assert (email, token) == ("env@example.com", "t")


def test_auth_falls_back_to_frozen_constant_without_env(monkeypatch):
    # Backward compat: no env token, module constant set -> use the constant.
    monkeypatch.setattr(jira_client, "JIRA_TOKEN", "frozen-token")
    monkeypatch.setattr(jira_client, "JIRA_EMAIL", "cfg@example.com")
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    email, token = jira_client._auth()
    assert (email, token) == ("cfg@example.com", "frozen-token")


# ── adaptive fetch: canonical statusCategory, not hardcoded status names ──────
def test_tickets_jql_uses_status_category_not_hardcoded_names():
    from jira_client import _tickets_jql
    jql = _tickets_jql("GHCMSE")
    assert "statusCategory != Done" in jql          # canonical, workflow-agnostic
    assert "GHCMSE" in jql
    # the old hardcoded names must be gone — they broke on other Jira workflows
    assert "Won't Do" not in jql and "Backlog" not in jql
