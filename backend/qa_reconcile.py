"""Main reconciliation — current main HEAD is authoritative, not the PR snapshot.

QA anchors a ticket's expected values to its PR. When a *later* ticket supersedes a
value, re-verifying the old PR still "passes" on the stale value (observed: INV-624's
PR set MRDT 3%; later tickets changed it to 2%; a re-run still passed on 3%). This
module re-anchors on current main and flags divergences so a run cannot PASS on a
value main no longer has.

The core reconcile() is pure — gh access is injected (fetch_pr_files / fetch_blob) so
the divergence logic is offline-testable. Live gh-API shells live at the bottom.
See docs/superpowers/specs/2026-06-29-main-reconciliation-design.md.
"""
import re

# Split a line at its first '=' / ':' / digit — the text before it is the line's
# "key" (e.g. "MRDT_RATE = 0.03" -> "MRDT_RATE", "mrdt: 3%" -> "mrdt"). Used to pair
# a PR-added line with the main line that superseded it.
_KEY_SPLIT = re.compile(r"[=:]|\d")


# Non-product files carry no acceptance-criteria values — a later main commit changing
# a test fixture / mock / snapshot / story is not a stale AC and must not diverge (it
# would otherwise wrongly block a pass on mock data — observed on INV-561/617).
_NONPRODUCT_MARKERS = ("/__tests__/", "/__mocks__/", "/__snapshots__/",
                       ".test.", ".spec.", ".stories.", ".snap")


def _is_reconcilable_path(path):
    p = (path or "").replace("\\", "/").lower()
    return not any(marker in p for marker in _NONPRODUCT_MARKERS)


def _added_lines(patch):
    """The new-side (+) lines a PR patch introduced (the '+++' header excluded)."""
    out = []
    for line in (patch or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def _is_trivial(line):
    """A line with no alphanumeric content carries no value to reconcile."""
    return not any(c.isalnum() for c in line)


def _line_key(line):
    s = line.strip()
    m = _KEY_SPLIT.search(s)
    key = s[:m.start()] if m else s
    return key.strip().rstrip("=:").strip()


def _find_main_hint(key, pr_line, main_lines):
    """The main line that shares the PR line's key but differs from it, else None."""
    if not key:
        return None
    for ml in main_lines:
        if ml and ml != pr_line and _line_key(ml) == key:
            return ml
    return None


def _file_divergences(repo, path, patch, pr_content, main_content):
    """PR-touched lines whose value main no longer has (deduped by key)."""
    main_lines = [ln.strip() for ln in (main_content or "").splitlines()]
    main_set = set(main_lines)
    out, seen = [], set()
    for added in _added_lines(patch):
        s = added.strip()
        if _is_trivial(s) or s in main_set:
            continue  # trivial, or the PR's addition survived unchanged in main
        key = _line_key(s)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "repo": repo, "path": path, "region": key or s,
            "pr_hint": s, "main_hint": _find_main_hint(key, s, main_lines),
        })
    return out


def reconcile(ticket_key, prs, *, fetch_pr_files, fetch_blob, main_ref="main"):
    """Reconcile a ticket's PRs against current main HEAD.

    For each PR-touched file, fetch the PR-head blob and the main-HEAD blob and flag
    every PR-added value main no longer has. Returns:
      {status, touched_files, main_snapshot, pr_snapshot, divergences, degraded_reason}
    main_snapshot/pr_snapshot are keyed "<repo>:<path>".
    """
    touched_files, main_snapshot, pr_snapshot, divergences = [], {}, {}, []
    status, degraded_reason = "ok", None
    try:
        for pr in prs or []:
            repo = pr.get("repo")
            pr_ref = pr.get("head_sha") or pr.get("branch")
            for f in fetch_pr_files(repo, pr.get("id")) or []:
                path = f.get("path")
                if not path or not _is_reconcilable_path(path):
                    continue  # skip non-existent / test / mock / snapshot / story files
                key = f"{repo}:{path}"
                touched_files.append({"repo": repo, "path": path})
                pr_content = fetch_blob(repo, pr_ref, path)
                main_content = fetch_blob(repo, main_ref, path)
                if pr_content is not None:
                    pr_snapshot[key] = pr_content
                if main_content is not None:
                    main_snapshot[key] = main_content
                if pr_content is None or main_content is None:
                    continue  # can't compare this file against main
                divergences.extend(
                    _file_divergences(repo, path, f.get("patch"), pr_content, main_content))
    except Exception as e:
        # gh unauthenticated / repo or PR not found / API failure: degrade, don't crash.
        # The caller emits TC-RECON (needs-review) so a degraded run never silently passes.
        status, degraded_reason = "degraded", str(e)
    return {
        "status": status,
        "touched_files": touched_files,
        "main_snapshot": main_snapshot,
        "pr_snapshot": pr_snapshot,
        "divergences": divergences,
        "degraded_reason": degraded_reason,
    }


def _degraded(reason):
    return {"status": "degraded", "touched_files": [], "main_snapshot": {},
            "pr_snapshot": {}, "divergences": [], "degraded_reason": reason}


def reconcile_live(ticket_key, prs, *, fetch_pr=None, fetch_pr_files=None, fetch_blob=None):
    """reconcile() against the live GitHub API, resolving each PR's head_sha first.

    A PR ref may carry only {repo, id}; we resolve the immutable head_sha via fetch_pr
    so blob fetches survive a deleted (merged) branch. Any resolution failure degrades
    the whole run (rather than silently reconciling nothing) so the guard TC fires.
    Fetchers default to github_client; injectable for tests."""
    import github_client as gc
    fetch_pr = fetch_pr or gc.fetch_pr
    fetch_pr_files = fetch_pr_files or gc.fetch_pr_files
    fetch_blob = fetch_blob or gc.fetch_blob

    resolved = []
    try:
        for pr in prs or []:
            if pr.get("head_sha"):
                resolved.append(pr)
                continue
            full = fetch_pr(pr.get("repo"), pr.get("id")) or {}
            resolved.append({**pr, "head_sha": full.get("head_sha"),
                             "branch": full.get("branch") or pr.get("branch")})
    except Exception as e:
        return _degraded(f"PR ref resolution failed: {e}")

    return reconcile(ticket_key, resolved,
                     fetch_pr_files=fetch_pr_files, fetch_blob=fetch_blob)


def fetch_ticket_pr_refs(ticket_key):
    """PR refs linked to a Linear ticket, via its GitHub attachment URLs.

    The GitHub↔Linear integration attaches the PR URL to the issue (sourceType
    'github'), which is the reliable ticket→PR link (branch names often omit the key).
    Returns [] when the ticket simply has no PR attachments; RAISES on a Linear API
    failure so reconcile_ticket degrades rather than silently passing PR-only. Live
    boundary code — the pure parsing it relies on is tested in github_client."""
    import os
    import httpx
    import instance_config as ic
    import github_client as gc

    ic.load_secrets_env()
    tok = os.environ.get("LINEAR_TOKEN")
    if not tok:
        return []  # no token to resolve links; the run proceeds without reconciliation
    m = re.match(r"^([A-Za-z]+)-(\d+)$", (ticket_key or "").strip())
    if not m:
        return []
    team, number = m.group(1).upper(), int(m.group(2))
    query = ('{ issues(first:1, filter:{ number:{ eq:%d }, team:{ key:{ eq:"%s" } } })'
             '{ nodes{ attachments{ nodes{ url } } } } }' % (number, team))
    with httpx.Client(timeout=20) as c:
        r = c.post("https://api.linear.app/graphql",
                   headers={"Authorization": tok, "Content-Type": "application/json"},
                   json={"query": query})
    r.raise_for_status()
    nodes = (((r.json().get("data") or {}).get("issues") or {}).get("nodes")) or []
    if not nodes:
        return []
    urls = [a.get("url") for a in (nodes[0].get("attachments") or {}).get("nodes", [])]
    return gc.pr_refs_from_urls(urls)


def build_reconcile_tcs(result):
    """AC-tied reconciliation TCs from a ReconcileResult, for the divergence guard.

    A degraded run emits one TC-RECON (needs-review) so it can't silently pass on
    unverified PR-only values. Each divergence that maps to a value (has a main_hint)
    becomes a needs-review TC-RECON-<n>. Because the ids are neither TC-API nor
    TC-UV-{3..6}, qa_scoring counts them (scoring), so the headline cannot be PASS
    while asserting a value main has changed. Unmapped divergences stay advisory (in
    the report's divergence section) and produce no scoring TC.
    """
    result = result or {}
    if result.get("status") == "degraded":
        return [{
            "id": "TC-RECON", "status": "needs-review",
            "note": "main reconciliation unavailable — values not verified against main",
        }]
    # Group mapped divergences (those paired to a changed main value) by file, so a
    # heavily-refactored file yields ONE needs-review TC, not dozens that would tank
    # the score. Unmapped divergences stay advisory (report only).
    by_file = {}
    for d in result.get("divergences") or []:
        if not d.get("main_hint"):
            continue
        by_file.setdefault((d.get("repo"), d.get("path")), []).append(d)
    tcs = []
    for (repo, path), ds in by_file.items():
        first = ds[0]
        extra = f" (+{len(ds) - 1} more in this file)" if len(ds) > 1 else ""
        tcs.append({
            "id": f"TC-RECON-{len(tcs) + 1}", "status": "needs-review",
            "repo": repo, "path": path,
            "note": (f"AC superseded by main in {path}: "
                     f"PR={first['pr_hint']}, main={first['main_hint']}{extra}"),
        })
    return tcs
