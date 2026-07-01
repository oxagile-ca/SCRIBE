"""GitHub VCS adapter — maps GitHub PR JSON to the pipeline's PR shape
(id/branch/destBranch/prStatus/repo, matching the Bitbucket adapter + _consolidate_prs).

Pure adapters (normalize_pr, prs_for_ticket_from_list) are unit-tested; the live
gh-API shells at the bottom are thin network boundary code (same pattern as
bitbucket_client.py) and are exercised via reconcile's injected fetchers.
"""
import re

_PR_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(url):
    """A GitHub PR URL -> {owner, repo, id}, else None.

    Handles the exact shape the GitHub↔Linear integration attaches to an issue
    (e.g. .../pull/238) plus trailing /files or #fragments. Non-PR / non-GitHub
    URLs (issues, Linear links) return None."""
    m = _PR_URL_RE.match((url or "").strip())
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2), "id": int(m.group(3))}


def pr_refs_from_urls(urls):
    """The GitHub PR refs among a list of URLs (drops issues / non-GitHub links)."""
    out = []
    for u in urls or []:
        ref = parse_pr_url(u)
        if ref:
            out.append(ref)
    return out


def _pr_status(pr: dict) -> str:
    """OPEN / MERGED / DECLINED from GitHub's (state, merged_at).

    GitHub has no 'declined' — a closed-but-not-merged PR is the equivalent, so it
    maps to DECLINED (which _consolidate_prs drops)."""
    state = (pr.get("state") or "").lower()
    if state == "open":
        return "OPEN"
    return "MERGED" if pr.get("merged_at") else "DECLINED"


def normalize_pr(pr: dict, repo: str) -> dict:
    """One GitHub PR JSON -> the pipeline PR shape."""
    head = pr.get("head") or {}
    return {
        "id": pr.get("number"),
        "title": pr.get("title"),
        "branch": head.get("ref"),
        "head_sha": head.get("sha"),
        "destBranch": (pr.get("base") or {}).get("ref"),
        "prStatus": _pr_status(pr),
        "repo": repo,
    }


def prs_for_ticket_from_list(prs, ticket_key: str, repo: str) -> list:
    """Normalized PRs whose source branch references the ticket key (case-insensitive)."""
    key = (ticket_key or "").upper()
    return [normalize_pr(pr, repo) for pr in (prs or [])
            if key in ((pr.get("head") or {}).get("ref") or "").upper()]


def pr_file_from_api(row: dict) -> dict:
    """One row of GET pulls/{n}/files -> reconcile's {path, status, patch} shape.

    GitHub omits `patch` for binary/large files; keep it None so reconcile falls back
    to a blob-only comparison for that file."""
    return {
        "path": row.get("filename"),
        "status": row.get("status"),
        "patch": row.get("patch"),
    }


# --- live gh-API shells (thin network boundary; not unit-tested, same as
# bitbucket_client — reconcile injects these and its logic is tested with fakes) ------

import os  # noqa: E402
import subprocess  # noqa: E402

import httpx  # noqa: E402

import instance_config as ic  # noqa: E402

GH_API = "https://api.github.com"
GH_OWNER = "Workabee-Technologies"  # vcs.org in instance.config.json

_TOKEN_CACHE = {}


def _gh_cli_token(attempts: int = 3) -> str:
    """The gh CLI's own credential via `gh auth token` (GH_CONFIG_DIR=~/.config/gh).

    This is the account that actually has Workabee org access (ankitguhe-afk); the
    .secrets.env ${secret:GITHUB_TOKEN} classic PAT does not (404s the org repos).
    See memory beeventory-qa-evidence-INV-617 / xinventory-git-account. Retries a few
    times because a transient subprocess hiccup would otherwise silently fall back to
    the broken PAT. '' if it can't resolve after all attempts."""
    env = dict(os.environ)
    env.setdefault("GH_CONFIG_DIR", os.path.expanduser("~/.config/gh"))
    # `gh auth token` honours GH_TOKEN/GITHUB_TOKEN over its stored credential. Something
    # earlier (load_secrets_env) may have injected the broken ${secret:GITHUB_TOKEN} PAT
    # into os.environ — strip both so gh returns its own working gho_ token.
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    for _ in range(max(1, attempts)):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                 text=True, env=env, timeout=15)
        except Exception:
            continue
        tok = out.stdout.strip() if out.returncode == 0 else ""
        if tok:
            return tok
    return ""


def _gh_token() -> str:
    """Resolve the GitHub token, preferring the working gh-CLI credential over the
    .secrets.env PAT. Only the good gh-CLI token is cached — a transient failure that
    forces the fallback must NOT poison the cache for the life of the backend process,
    or every later reconcile would degrade until restart."""
    if _TOKEN_CACHE.get("token"):
        return _TOKEN_CACHE["token"]
    tok = _gh_cli_token()
    if tok:
        _TOKEN_CACHE["token"] = tok
        return tok
    # non-clobbering fallback; not cached so the next call retries gh
    return (ic.read_secrets_file().get("GITHUB_TOKEN", "")
            or os.environ.get("GITHUB_TOKEN", ""))


def _gh_headers() -> dict:
    tok = _gh_token()
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def fetch_pr(repo: str, pr_id, owner: str = GH_OWNER) -> dict:
    """A single PR as the normalized shape (carries head_sha for blob fetches)."""
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GH_API}/repos/{owner}/{repo}/pulls/{pr_id}", headers=_gh_headers())
        r.raise_for_status()
        return normalize_pr(r.json(), repo)


def fetch_pr_files(repo: str, pr_id, owner: str = GH_OWNER) -> list:
    """All changed files of a PR (paginated). Raises on API error so reconcile degrades."""
    out, page = [], 1
    with httpx.Client(timeout=30) as c:
        while True:
            r = c.get(f"{GH_API}/repos/{owner}/{repo}/pulls/{pr_id}/files",
                      params={"per_page": 100, "page": page}, headers=_gh_headers())
            r.raise_for_status()
            rows = r.json() or []
            out.extend(pr_file_from_api(row) for row in rows)
            if len(rows) < 100:
                break
            page += 1
    return out


def fetch_blob(repo: str, ref: str, path: str, owner: str = GH_OWNER):
    """Raw file content at a ref (branch or sha), or None if the path is absent (404).

    404 is expected — a file added in the PR won't exist on main yet — so it must not
    degrade the whole run; other HTTP errors raise so reconcile marks degraded."""
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GH_API}/repos/{owner}/{repo}/contents/{path}",
                  params={"ref": ref},
                  headers={**_gh_headers(), "Accept": "application/vnd.github.raw"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text
