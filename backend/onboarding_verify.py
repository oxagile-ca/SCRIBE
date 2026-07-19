"""Live connection checks for the onboarding wizard.

Each public ``verify_*`` coroutine makes ONE cheap live call for an integration the
user just configured and returns a small ``{ok, detail, hint}`` result the wizard
renders as a green ✓ (``detail``) or red ✗ (``hint``). The point is to catch the
silent-bad-token class of failure — e.g. a Linear token that authenticates but can't
see the configured team — at onboarding time instead of when the board comes up empty.

House style: the network call is a thin wrapper; the decision logic lives in pure
``interpret_*`` functions so it can be unit-tested without mocking httpx. Nothing here
raises, and no secret value is ever put in a result string.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

LINEAR_API = "https://api.linear.app/graphql"
GH_API = "https://api.github.com"
ANTHROPIC_MODELS = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

Result = dict  # {"ok": bool, "detail": str, "hint": str}


def _ok(detail: str) -> Result:
    return {"ok": True, "detail": detail, "hint": ""}


def _fail(hint: str) -> Result:
    return {"ok": False, "detail": "", "hint": hint}


# ── issue tracker ────────────────────────────────────────────────────────────

_LINEAR_VERIFY_QUERY = "{ viewer { id name email } teams(first: 250) { nodes { key } } }"


def interpret_linear(status: int, data: dict | None, projects: list) -> Result:
    """Decide the verdict from a Linear GraphQL response.

    Catches today's real bug: a token that authenticates fine but belongs to a
    different workspace, so the configured team (e.g. NOR) isn't visible.
    """
    if status in (401, 403):
        return _fail(f"Linear rejected the token (HTTP {status}).")
    data = data or {}
    if data.get("errors"):
        msg = ((data["errors"] or [{}])[0] or {}).get("message", "authentication error")
        return _fail(f"Linear rejected the token: {msg}")
    d = data.get("data") or {}
    viewer = d.get("viewer") or {}
    if not viewer:
        return _fail(f"Linear returned no viewer (HTTP {status}) — token may be invalid.")
    teams = {(t or {}).get("key") for t in ((d.get("teams") or {}).get("nodes") or [])}
    who = viewer.get("email") or viewer.get("name") or "unknown user"
    missing = [p for p in (projects or []) if p not in teams]
    if missing:
        return _fail(
            f"Token is valid (as {who}) but can't see team(s) {', '.join(missing)} — "
            f"this is likely a token for a different Linear workspace."
        )
    if projects:
        return _ok(f"Connected as {who}; team(s) {', '.join(projects)} visible.")
    return _ok(f"Connected as {who}.")


async def verify_issue_tracker(answers: dict) -> Result:
    it = (answers or {}).get("issueTracker") or {}
    itype = (it.get("type") or "").lower()
    token = it.get("token") or ""
    projects = it.get("projects") or []
    if not token:
        return _fail("No issue-tracker token provided.")
    if itype != "linear":
        # Jira/Azure/GitHub-issues live checks aren't implemented yet; don't block.
        return _ok(f"{itype or 'tracker'}: token present (no live check for this type yet).")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                LINEAR_API,
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"query": _LINEAR_VERIFY_QUERY},
            )
    except Exception as e:  # noqa: BLE001 — surface a reachability hint, never raise
        return _fail(f"Could not reach Linear: {e}")
    try:
        data = r.json()
    except Exception:
        data = None
    return interpret_linear(r.status_code, data, projects)


# ── version control ──────────────────────────────────────────────────────────

def owner_repo_from_vcs(vcs: dict) -> tuple[str, str]:
    """Resolve (owner, repo) from the wizard's vcs answers.

    ``org`` is usually a URL like https://github.com/OWNER/REPO (or .../OWNER); ``repos``
    is a list of bare repo names. Prefer repos[0] for the repo, org's path for the owner.
    """
    vcs = vcs or {}
    org = (vcs.get("org") or "").strip()
    repos = vcs.get("repos") or []
    path_parts: list[str] = []
    if org.startswith("http"):
        path_parts = [p for p in urlparse(org).path.split("/") if p]
    elif org:
        path_parts = [p for p in org.split("/") if p]
    owner = path_parts[0] if path_parts else ""
    repo = (repos[0] if repos else "") or (path_parts[1] if len(path_parts) > 1 else "")
    return owner, repo


def interpret_github(status: int, owner: str, repo: str) -> Result:
    if status == 200:
        return _ok(f"Repo {owner}/{repo} accessible.")
    if status == 401:
        return _fail("GitHub rejected the token (401) — check the PAT.")
    if status == 403:
        return _fail("GitHub returned 403 — token lacks scope or is rate-limited.")
    if status == 404:
        return _fail(f"{owner}/{repo} not found, or the token can't access it (404).")
    return _fail(f"GitHub returned HTTP {status}.")


async def verify_vcs(answers: dict) -> Result:
    vcs = (answers or {}).get("vcs") or {}
    vtype = (vcs.get("type") or "").lower()
    token = vcs.get("token") or ""
    if not token:
        return _fail("No version-control token provided.")
    if vtype != "github":
        return _ok(f"{vtype or 'vcs'}: token present (no live check for this type yet).")
    owner, repo = owner_repo_from_vcs(vcs)
    if not owner or not repo:
        return _fail("Need an owner (org URL) and at least one repo to check GitHub access.")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{GH_API}/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not reach GitHub: {e}")
    return interpret_github(r.status_code, owner, repo)


# ── test environment ─────────────────────────────────────────────────────────

def interpret_environment(status: int, url: str) -> Result:
    if status < 400:
        return _ok(f"Reachable — {url} (HTTP {status}).")
    return _fail(f"{url} returned HTTP {status} — check the URL is the app's root.")


async def verify_environment(answers: dict) -> Result:
    env = (answers or {}).get("environments") or {}
    urls = env.get("staticUrls") or []
    url = (urls[0] if urls else "") or (env.get("readinessUrlPattern") or "")
    if not url:
        return _fail("No environment URL to check.")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not reach {url}: {e}")
    return interpret_environment(r.status_code, url)


# ── anthropic key ────────────────────────────────────────────────────────────

def interpret_anthropic(status: int, has_key: bool) -> Result:
    if not has_key:
        return _ok("No key set — the runner will use its default credential.")
    if status == 200:
        return _ok("Anthropic key valid.")
    if status == 401:
        return _fail("Anthropic rejected the key (401).")
    return _fail(f"Anthropic returned HTTP {status}.")


async def verify_anthropic(answers: dict) -> Result:
    key = (answers or {}).get("anthropicKey") or ""
    if not key:
        return interpret_anthropic(0, has_key=False)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                ANTHROPIC_MODELS,
                headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
            )
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not reach Anthropic: {e}")
    return interpret_anthropic(r.status_code, has_key=True)


# ── dispatch ─────────────────────────────────────────────────────────────────

_VERIFIERS = {
    "issueTracker": verify_issue_tracker,
    "vcs": verify_vcs,
    "environment": verify_environment,
    "anthropic": verify_anthropic,
}


async def verify(target: str, answers: dict) -> Result:
    fn = _VERIFIERS.get(target)
    if not fn:
        return _fail(f"Unknown verify target: {target!r}.")
    return await fn(answers)
