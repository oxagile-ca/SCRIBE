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
