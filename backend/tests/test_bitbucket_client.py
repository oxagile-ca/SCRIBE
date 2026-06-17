import json
import pytest
from unittest.mock import AsyncMock, patch

from bitbucket_client import get_file


@pytest.mark.asyncio
async def test_get_file_returns_text_on_200():
    # Branch contains "/", so get_file resolves it to a commit hash first
    # (BB's /src/ endpoint rejects slashed branches even URL-encoded).
    branch_resp = AsyncMock()
    branch_resp.status_code = 200
    branch_resp.json = lambda: {"target": {"hash": "abc123"}}

    file_resp = AsyncMock()
    file_resp.status_code = 200
    file_resp.text = '{"deployable":"service"}'

    with patch("bitbucket_client.httpx.AsyncClient") as cm:
        client = cm.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=[branch_resp, file_resp])

        out = await get_file("service-a", "feature/PROJ-404", "ci/manifest.json")

    assert out == '{"deployable":"service"}'
    # Confirm the file fetch went to the resolved SHA, not the slashed branch.
    assert "/src/abc123/" in client.get.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_get_file_returns_none_on_404():
    mock_resp = AsyncMock()
    mock_resp.status_code = 404
    mock_resp.text = ""

    with patch("bitbucket_client.httpx.AsyncClient") as cm:
        client = cm.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=mock_resp)

        out = await get_file("service-a", "main", "ci/missing.json")

    assert out is None


@pytest.mark.asyncio
async def test_get_file_returns_none_on_exception():
    with patch("bitbucket_client.httpx.AsyncClient") as cm:
        client = cm.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=Exception("boom"))

        out = await get_file("service-a", "main", "ci/manifest.json")

    assert out is None
