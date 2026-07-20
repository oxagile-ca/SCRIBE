"""Per-ticket store of user-added QA test cases, kept LOCAL to SCRIBE.

Intentionally NOT written back to the issue tracker: added cases live only here, so no
write token is needed and nothing is posted externally. The dashboard shows them on the
ticket card; qa_targets merges them into the run scope so they are actually tested.

File: ~/qa-dashboard/test-cases.json  ->  { "<TICKET-KEY>": [ {id, text, ts}, ... ] }
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import uuid
from typing import Optional

STORE_PATH = os.environ.get(
    "SCRIBE_TEST_CASES", os.path.expanduser("~/qa-dashboard/test-cases.json")
)
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic swap so a crash mid-write can't corrupt the store


def list_cases(key: str, path: Optional[str] = None) -> list:
    """User-added cases for a ticket, oldest first: [{id, text, ts}, ...]."""
    path = path or STORE_PATH
    with _LOCK:
        return list(_load(path).get(key, []))


def add_case(key: str, text: str, path: Optional[str] = None) -> Optional[dict]:
    """Append a case; returns it, or None if the text is blank (nothing stored)."""
    text = (text or "").strip()
    if not text:
        return None
    path = path or STORE_PATH
    case = {"id": uuid.uuid4().hex[:12], "text": text, "ts": _now_iso()}
    with _LOCK:
        data = _load(path)
        data.setdefault(key, []).append(case)
        _save(path, data)
    return case


def delete_case(key: str, case_id: str, path: Optional[str] = None) -> bool:
    """Remove a case by id; returns True if one was removed."""
    path = path or STORE_PATH
    with _LOCK:
        data = _load(path)
        cases = data.get(key, [])
        remaining = [c for c in cases if c.get("id") != case_id]
        if len(remaining) == len(cases):
            return False
        if remaining:
            data[key] = remaining
        else:
            data.pop(key, None)
        _save(path, data)
    return True


def update_case(key: str, case_id: str, text: str,
                path: Optional[str] = None) -> Optional[dict]:
    """Edit a case's text in place.

    Returns the updated case, or None when the text is blank or the id is not
    found (nothing is written in either case). The case's id, ts, and position
    in the list are preserved: the list is oldest-first and qa_targets builds
    the run scope in that order, so an edit must not reorder it.
    """
    text = (text or "").strip()
    if not text:
        return None
    path = path or STORE_PATH
    with _LOCK:
        data = _load(path)
        for case in data.get(key, []):
            if case.get("id") == case_id:
                case["text"] = text
                _save(path, data)
                return dict(case)
    return None


def texts_for(key: str, path: Optional[str] = None) -> list:
    """Just the case strings for a ticket — used to merge into the QA run scope."""
    return [c.get("text", "") for c in list_cases(key, path) if c.get("text")]
