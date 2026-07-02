"""Gated deterministic API smoke — fire the ticket's read endpoints, assert, write evidence.

Runs only for API-relevant tickets (see qa_api_gate), code-driven (no agent discretion).
GET/read requests only; mutations skipped. Strictness (spec §2): 2xx→pass, 5xx/timeout→fail,
4xx→needs-review (likely a stale fixture id after a reseed). Results are ADVISORY — they
render as TC-API-* but qa_scoring excludes them from the headline. HTTP + token mint are
injected so the core is offline-testable. See
docs/superpowers/specs/2026-06-29-gated-api-smoke-design.md §3.3.
"""
import json
import os
import re

# PII patterns scrubbed from anything written to evidence (values never leave as-is).
_PII = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                       # email
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                          # SSN
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),                        # card
]


def _scrub(obj):
    """Recursively redact PII in string values; other types pass through."""
    if isinstance(obj, str):
        s = obj
        for pat in _PII:
            s = pat.sub("[REDACTED]", s)
        return s
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _norm(path):
    return (path or "").rstrip("/").lower()


def select_requests(requests, endpoints):
    """GET requests whose path matches an endpoint (equal, or endpoint is a path prefix).
    Mutations are never selected — the smoke is read-only."""
    eps = [_norm(e) for e in (endpoints or []) if e]
    out = []
    for r in requests or []:
        if (r.get("method") or "").upper() != "GET":
            continue
        rp = _norm(r.get("path"))
        if any(rp == e or rp.startswith(e + "/") or e == rp for e in eps):
            out.append(r)
    return out


def _response_keys(js):
    if isinstance(js, dict):
        return sorted(js.keys())
    if isinstance(js, list) and js and isinstance(js[0], dict):
        return sorted(js[0].keys())
    return []


def _status_verdict(status, error):
    """(tc_status, note) from an HTTP status / transport error."""
    if error or status is None:
        return "fail", f"request failed: {error or 'no response'}"
    if 200 <= status < 300:
        return "pass", f"{status} OK"
    if 400 <= status < 500:
        return "needs-review", f"{status} — likely a stale fixture id after a reseed, not a regression"
    if status >= 500:
        return "fail", f"{status} server error"
    return "needs-review", f"unexpected status {status}"


async def _httpx_fetch(request, api_base, token):
    """Default fetch: GET the request's full path against the configured env's HOST with the
    bearer (~10s). Uses api_base's scheme+host only — the request path already carries
    /api/v1/…, so joining api_base (which includes the prefix) would double it."""
    import httpx
    from urllib.parse import urlsplit
    b = urlsplit(api_base)
    host = f"{b.scheme}://{b.netloc}" if b.netloc else api_base.rstrip("/")
    url = host + request["path"]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, params=request.get("query") or {}, headers=headers)
    except Exception as e:
        return {"status": None, "json": None, "error": type(e).__name__}
    try:
        js = r.json()
    except Exception:
        js = None
    return {"status": r.status_code, "json": js, "error": None}


def _default_requests():
    import qa_postman
    from instance_config import load_instance_config
    coll = ((load_instance_config() or {}).get("api") or {}).get("postmanCollectionPath")
    return qa_postman.load_requests(coll) if coll else []


async def run(ticket_key, run_name, api_base, endpoints, *,
              requests=None, mint=None, fetch=None, evidence_dir=None):
    """Fire the ticket's matched read endpoints and return advisory TC-API-* results.

    Any failure degrades to a blocked TC rather than raising (never aborts finalize)."""
    requests = requests if requests is not None else _default_requests()
    fetch = fetch or _httpx_fetch
    if evidence_dir is None:
        import config
        evidence_dir = config.EVIDENCE_DIR

    selected = select_requests(requests, endpoints)
    if not selected:
        return []

    # Mint the id_token; no creds / mint failure -> a single blocked TC (not a hard fail).
    try:
        if mint is None:
            import qa_auth
            mint = qa_auth.mint_tokens
        tokens = await mint()
        token = (tokens or {}).get("id_token")
    except Exception as e:
        token = None
        _mint_err = type(e).__name__
    else:
        _mint_err = None
    if not token:
        note = ("API auth not configured" if _mint_err is None
                else f"API auth failed: {_mint_err}")
        return [{"id": "TC-API", "title": "API smoke", "status": "blocked",
                 "note": note, "evidence": None}]

    run_dir = os.path.join(evidence_dir, ticket_key, "runs", run_name, "automated")
    results = []
    for i, req in enumerate(selected, 1):
        tc_id = f"TC-API-{req.get('group', 'API')}-{i}".replace(" ", "")
        try:
            resp = await fetch(req, api_base, token)
        except Exception as e:
            resp = {"status": None, "json": None, "error": type(e).__name__}
        status, note = _status_verdict(resp.get("status"), resp.get("error"))
        record = _scrub({
            "request": {"method": req.get("method"), "path": req.get("path"),
                        "query": req.get("query") or {}},
            "status": resp.get("status"),
            "ok": status == "pass",
            "responseKeys": _response_keys(resp.get("json")),
            "asserted": {"endpoint": req.get("path"), "verdict": status},
        })
        rel = os.path.join("automated", tc_id, f"api-{req.get('name', 'req')}.json").replace(" ", "")
        try:
            os.makedirs(os.path.join(run_dir, tc_id), exist_ok=True)
            with open(os.path.join(evidence_dir, ticket_key, "runs", run_name, rel),
                      "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
        except OSError:
            rel = None
        results.append({"id": tc_id, "title": f"{req.get('method')} {req.get('path')}",
                        "status": status, "note": note, "evidence": rel})
    return results
