"""Tests for qa_api_smoke — fire matched GET reads, map status, write scrubbed evidence.

HTTP + token mint are injected so the runner is tested offline. Spec §3.3 / §8.
"""
import asyncio
import json
import os
import qa_api_smoke

REQS = [
    {"method": "GET", "name": "get invoice", "group": "SalesOrder",
     "url": "https://api.x/api/v1/so/invoice", "path": "/api/v1/so/invoice",
     "query": {"booking_id": "856"}, "headers": {}, "body": None,
     "raw": "https://api.x/api/v1/so/invoice?booking_id=856"},
    {"method": "GET", "name": "get profile", "group": "User",
     "url": "https://api.x/api/v1/user/profile", "path": "/api/v1/user/profile",
     "query": {}, "headers": {}, "body": None, "raw": "https://api.x/api/v1/user/profile"},
    {"method": "POST", "name": "create li", "group": "SalesOrder",
     "url": "https://api.x/api/v1/so/lineitem", "path": "/api/v1/so/lineitem",
     "query": {}, "headers": {}, "body": "{}", "raw": "https://api.x/api/v1/so/lineitem"},
]


def _mint_ok(token="SECRET-ID-TOKEN"):
    async def _m():
        return {"id_token": token}
    return _m


def _fetch(responses):
    """responses: {path: {"status":int,"json":obj,"error":str|None}} -> async fetch stub.
    Records the token it was called with so tests can assert it never leaks to disk."""
    calls = {}

    async def _f(request, api_base, token):
        calls["token"] = token
        return responses.get(request["path"], {"status": 200, "json": {}, "error": None})
    _f.calls = calls
    return _f


def _run(tmp_path, endpoints, responses, mint=None):
    fetch = _fetch(responses)
    tcs = asyncio.run(qa_api_smoke.run(
        "INV-9", "run-1", "https://api.x", endpoints,
        requests=REQS, mint=mint or _mint_ok(), fetch=fetch,
        evidence_dir=str(tmp_path)))
    return tcs, fetch


def test_select_requests_matches_get_by_path():
    sel = qa_api_smoke.select_requests(REQS, ["/api/v1/so/invoice"])
    assert [r["name"] for r in sel] == ["get invoice"]              # GET only, path match
    # POST /so/lineitem is excluded even if its path is named
    assert qa_api_smoke.select_requests(REQS, ["/api/v1/so/lineitem"]) == []


def test_run_pass_on_2xx_writes_evidence_without_token(tmp_path):
    tcs, fetch = _run(tmp_path, ["/api/v1/so/invoice"],
                      {"/api/v1/so/invoice": {"status": 200,
                                              "json": {"invoice": {}, "total_due": 100},
                                              "error": None}})
    assert len(tcs) == 1
    assert tcs[0]["status"] == "pass"
    assert tcs[0]["id"].startswith("TC-API-")
    # evidence path is relative to the run dir (the report's convention)
    ev = os.path.join(str(tmp_path), "INV-9", "runs", "run-1", tcs[0]["evidence"])
    body = open(ev, encoding="utf-8").read()
    rec = json.loads(body)
    assert rec["status"] == 200 and rec["ok"] is True
    assert set(rec["responseKeys"]) == {"invoice", "total_due"}
    assert "SECRET-ID-TOKEN" not in body                            # token never written
    assert fetch.calls["token"] == "SECRET-ID-TOKEN"               # but WAS used to call


def test_run_fail_on_5xx_and_timeout(tmp_path):
    tcs, _ = _run(tmp_path, ["/api/v1/so/invoice", "/api/v1/user/profile"],
                  {"/api/v1/so/invoice": {"status": 500, "json": None, "error": None},
                   "/api/v1/user/profile": {"status": None, "json": None, "error": "timeout"}})
    by_path = {t["id"]: t["status"] for t in tcs}
    assert set(by_path.values()) == {"fail"}                        # 5xx and timeout both fail


def test_run_needs_review_on_4xx(tmp_path):
    tcs, _ = _run(tmp_path, ["/api/v1/so/invoice"],
                  {"/api/v1/so/invoice": {"status": 404, "json": None, "error": None}})
    assert tcs[0]["status"] == "needs-review"                       # stale fixture, not a regression


def test_run_blocked_when_no_creds(tmp_path):
    async def _no_creds():
        return {}
    tcs, _ = _run(tmp_path, ["/api/v1/so/invoice"], {}, mint=_no_creds)
    assert len(tcs) == 1
    assert tcs[0]["status"] == "blocked"
    assert "auth" in tcs[0]["note"].lower()


def test_run_empty_when_no_matching_endpoints(tmp_path):
    tcs, _ = _run(tmp_path, ["/api/v1/nope"], {})
    assert tcs == []


def test_scrub_redacts_pii():
    scrubbed = qa_api_smoke._scrub({"email": "jane@doe.com", "n": 5,
                                    "nested": ["card 4111 1111 1111 1111"]})
    assert "jane@doe.com" not in json.dumps(scrubbed)
    assert "4111" not in json.dumps(scrubbed)
    assert scrubbed["n"] == 5
