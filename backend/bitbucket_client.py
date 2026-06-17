import base64
import json
import os
from typing import Optional

import httpx

BB_BASE = "https://api.bitbucket.org/2.0"
BB_WORKSPACE = "acme"


def _creds():
    user = os.environ.get("BITBUCKET_USERNAME", "")
    token = os.environ.get("BITBUCKET_TOKEN", "")
    if not token:
        mcp_path = os.path.expanduser("~/.claude/mcp.json")
        if os.path.exists(mcp_path):
            with open(mcp_path) as f:
                data = json.load(f)
            env = data.get("env", {})
            user = user or env.get("BITBUCKET_USERNAME", "")
            token = token or env.get("BITBUCKET_TOKEN", "")
    return user, token


def _headers():
    user, token = _creds()
    if not (user and token):
        return {"Accept": "application/json"}
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


async def get_pr(repo: str, pr_id: str) -> dict:
    """Fetch a single PR by repo (bare name) and PR id."""
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/pullrequests/{pr_id}"
    async with httpx.AsyncClient(timeout=15) as c:
        resp = await c.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def find_prs_for_ticket(repo: str, ticket_key: str) -> list[dict]:
    """Search open PRs whose source branch contains the ticket key."""
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/pullrequests"
    params = {
        "q": f'source.branch.name~"{ticket_key}"',
        "fields": "values.id,values.title,values.state,values.source,values.destination,values.links",
        "pagelen": 20,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(url, params=params, headers=_headers())
            if resp.status_code != 200:
                return []
            return resp.json().get("values", [])
    except Exception:
        return []


async def get_pr_diff(repo: str, pr_id: str) -> str:
    """Fetch the unified diff for a PR. Follows the 302 redirect to S3."""
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/pullrequests/{pr_id}/diff"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            resp = await c.get(url, headers=_headers())
            if resp.status_code != 200:
                return ""
            return resp.text
    except Exception:
        return ""


async def get_pr_diffstat(repo: str, pr_id: str) -> list[dict]:
    """Return list of {path, lines_added, lines_removed, status} for a PR."""
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/pullrequests/{pr_id}/diffstat"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            resp = await c.get(url, headers=_headers())
            if resp.status_code != 200:
                return []
            data = resp.json()
            result = []
            for entry in data.get("values", []):
                new = entry.get("new") or {}
                old = entry.get("old") or {}
                path = new.get("path") or old.get("path") or ""
                result.append({
                    "path": path,
                    "lines_added": entry.get("lines_added", 0),
                    "lines_removed": entry.get("lines_removed", 0),
                    "status": entry.get("status", ""),
                })
            return result
    except Exception:
        return []


async def post_pr_comment(repo: str, pr_id: str, message: str) -> bool:
    """Post a comment on a PR. Returns True on success."""
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/pullrequests/{pr_id}/comments"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                url,
                headers={**_headers(), "Content-Type": "application/json"},
                json={"content": {"raw": message}},
            )
            return resp.status_code in (200, 201)
    except Exception:
        return False


async def get_repo_branch(repo: str, branch: str) -> dict:
    """Check if a branch exists on a repo. Returns branch info or None."""
    encoded = branch.replace("/", "%2F")
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/refs/branches/{encoded}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(url, headers=_headers())
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception:
        return None


async def check_auth() -> bool:
    """Return True if the configured credentials can reach the BB API."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{BB_BASE}/user", headers=_headers())
            return resp.status_code == 200
    except Exception:
        return False


async def get_file(repo: str, branch: str, path: str) -> Optional[str]:
    """Fetch raw file contents from a repo's branch. Returns text on 200, None otherwise.

    Bitbucket's /src/{ref}/ endpoint cannot reliably accept a slashed branch name
    even URL-encoded — it splits on the first slash and treats the prefix as a
    commit SHA (returns 404 "Commit not found"). Resolve branch → commit hash
    first, then fetch by hash. Branches without slashes (e.g. "main") still work
    via the direct path so we only do the extra hop when needed."""
    if "/" in branch:
        info = await get_repo_branch(repo, branch)
        if not info:
            return None
        ref = info.get("target", {}).get("hash") or branch
    else:
        ref = branch
    url = f"{BB_BASE}/repositories/{BB_WORKSPACE}/{repo}/src/{ref}/{path}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            resp = await c.get(url, headers=_headers())
            if resp.status_code != 200:
                return None
            return resp.text
    except Exception:
        return None
