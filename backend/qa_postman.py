"""Parse a Postman v2.1 collection into runnable Request dicts.

Richer than onboarding._parse_postman_endpoints (which only counts endpoints): resolves
collection variables ({{BASE_URL}} etc.) and returns method/path/query/headers/body so
qa_api_smoke can actually fire the read requests. See
docs/superpowers/specs/2026-06-29-gated-api-smoke-design.md §3.1.
"""
import json
import os
import re
from urllib.parse import urlsplit, parse_qsl

_VAR_RE = re.compile(r"\{\{([^}]+)\}\}")


def _collection_vars(coll: dict) -> dict:
    """key -> first NON-EMPTY value (a collection often lists an empty override after the
    real default, e.g. two BASE_URL entries)."""
    out = {}
    for v in coll.get("variable") or []:
        k, val = v.get("key"), v.get("value")
        if k and val and k not in out:
            out[k] = val
    return out


def _resolve(text: str, variables: dict) -> str:
    if not text:
        return text or ""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1).strip(), ""), text)


def _headers(req: dict, variables: dict) -> dict:
    out = {}
    for h in req.get("header") or []:
        k = h.get("key")
        if k:
            out[k] = _resolve(h.get("value") or "", variables)
    return out


def _body(req: dict, variables: dict):
    b = req.get("body")
    if isinstance(b, dict) and b.get("mode") == "raw" and b.get("raw"):
        return _resolve(b["raw"], variables)
    return None


def _query(url_obj, raw: str, variables: dict) -> dict:
    """Prefer the structured url.query; fall back to parsing the resolved raw."""
    out = {}
    if isinstance(url_obj, dict):
        for q in url_obj.get("query") or []:
            k = q.get("key")
            if k and not q.get("disabled"):
                out[k] = _resolve(q.get("value") or "", variables)
    if not out and "?" in (raw or ""):
        out = {k: v for k, v in parse_qsl(urlsplit(raw).query)}
    return out


def load_requests(path: str) -> list:
    """All requests in a Postman collection as
    {method, name, group, url, path, query, headers, body, raw}. [] on any error."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            coll = json.load(fh)
    except Exception:
        return []

    variables = _collection_vars(coll)
    out = []

    def walk(items, group):
        for it in items:
            if "item" in it:
                walk(it["item"], it.get("name", group))
                continue
            req = it.get("request") or {}
            url_obj = req.get("url")
            raw_tmpl = url_obj if isinstance(url_obj, str) else (
                (url_obj or {}).get("raw", "") if isinstance(url_obj, dict) else "")
            raw = _resolve(raw_tmpl, variables)
            url_no_query = raw.split("?", 1)[0]
            out.append({
                "method": (req.get("method") or "GET").upper(),
                "name": it.get("name", ""),
                "group": group,
                "url": url_no_query,
                "path": urlsplit(url_no_query).path,
                "query": _query(url_obj, raw, variables),
                "headers": _headers(req, variables),
                "body": _body(req, variables),
                "raw": raw,
            })

    walk(coll.get("item") or [], (coll.get("info") or {}).get("name", "API"))
    return out
