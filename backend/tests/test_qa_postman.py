"""Tests for qa_postman — Postman v2.1 collection -> runnable Request dicts.

Pure parsing over a fixture mini-collection (spec §3.1 / §8).
"""
import json
import qa_postman


COLLECTION = {
    "info": {"name": "Xinventory API"},
    "variable": [
        {"key": "BASE_URL", "value": "https://api.example"},
        {"key": "BASE_URL", "value": ""},          # duplicate empty -> first non-empty wins
        {"key": "id_token", "value": ""},
    ],
    "item": [
        {"name": "User", "item": [
            {"name": "get user profile", "request": {
                "method": "GET",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "url": {"raw": "{{BASE_URL}}/api/v1/user/profile",
                        "path": ["api", "v1", "user", "profile"]},
            }},
        ]},
        {"name": "SalesOrder", "item": [
            {"name": "get invoice", "request": {
                "method": "GET",
                "header": [],
                "url": {"raw": "{{BASE_URL}}/api/v1/so/invoice?booking_id=856",
                        "path": ["api", "v1", "so", "invoice"],
                        "query": [{"key": "booking_id", "value": "856"}]},
            }},
            {"name": "create line item", "request": {
                "method": "POST",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": "{\"x\": 1}"},
                "url": {"raw": "{{BASE_URL}}/api/v1/so/lineitem",
                        "path": ["api", "v1", "so", "lineitem"]},
            }},
        ]},
    ],
}


def _write(tmp_path):
    p = tmp_path / "coll.json"
    p.write_text(json.dumps(COLLECTION), encoding="utf-8")
    return str(p)


def test_load_requests_resolves_base_url_and_fields(tmp_path):
    reqs = qa_postman.load_requests(_write(tmp_path))
    prof = next(r for r in reqs if r["name"] == "get user profile")
    assert prof["method"] == "GET"
    assert prof["group"] == "User"                              # folder name
    assert prof["url"] == "https://api.example/api/v1/user/profile"   # {{BASE_URL}} resolved
    assert prof["path"] == "/api/v1/user/profile"              # host+query stripped, for matching
    assert prof["query"] == {}
    assert prof["headers"] == {"Content-Type": "application/json"}
    assert prof["body"] is None


def test_load_requests_extracts_query_and_keeps_raw(tmp_path):
    reqs = qa_postman.load_requests(_write(tmp_path))
    inv = next(r for r in reqs if r["name"] == "get invoice")
    assert inv["path"] == "/api/v1/so/invoice"
    assert inv["query"] == {"booking_id": "856"}
    assert inv["url"] == "https://api.example/api/v1/so/invoice"   # url has no query
    assert inv["raw"] == "https://api.example/api/v1/so/invoice?booking_id=856"  # raw keeps it


def test_load_requests_includes_post_body(tmp_path):
    reqs = qa_postman.load_requests(_write(tmp_path))
    li = next(r for r in reqs if r["name"] == "create line item")
    assert li["method"] == "POST"
    assert li["group"] == "SalesOrder"
    assert li["body"] == '{"x": 1}'


def test_load_requests_missing_file_returns_empty():
    assert qa_postman.load_requests("/no/such/collection.json") == []
