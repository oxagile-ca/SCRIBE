"""Append-only ledger of per-call AI usage (tokens + USD) for SCRIBE.

Owns ~/qa-dashboard/usage-ledger.jsonl and the pure parsers that extract usage
from `claude -p --output-format stream-json` events. council.py and chat.py
import the parsers and call record() once per completed turn.
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Optional

LEDGER_PATH = os.environ.get(
    "SCRIBE_USAGE_LEDGER", os.path.expanduser("~/qa-dashboard/usage-ledger.jsonl")
)
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_result_usage(event: dict) -> dict:
    """Pull cost + token counts from a stream-json `result` event. All-zero default."""
    u = event.get("usage") or {}
    return {
        "cost_usd": event.get("total_cost_usd", 0.0) or 0.0,
        "duration_ms": event.get("duration_ms", 0) or 0,
        "input_tokens": u.get("input_tokens", 0) or 0,
        "output_tokens": u.get("output_tokens", 0) or 0,
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": u.get("cache_read_input_tokens", 0) or 0,
    }


def parse_model_from_init(event: dict) -> Optional[str]:
    """Return the model id from a `system`/`init` event, else None."""
    if event.get("type") == "system" and event.get("subtype") == "init":
        return event.get("model")
    return None


def record(*, task: str, ticket: Optional[str], pipeline_id: Optional[str],
           model: Optional[str], usage: dict, session_id: str = "",
           is_error: bool = False, ts: Optional[str] = None,
           path: Optional[str] = None) -> dict:
    path = path or LEDGER_PATH
    rec = {
        "ts": ts or _now_iso(),
        "ticket": ticket,
        "pipeline_id": pipeline_id,
        "task": task,
        "model": model or "default",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
        "duration_ms": usage.get("duration_ms", 0),
        "is_error": bool(is_error),
        "session_id": session_id,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def _iter(path: str):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def aggregate_for_ticket(key: str, path: Optional[str] = None) -> dict:
    path = path or LEDGER_PATH
    groups: dict = {}
    tot_in = tot_out = 0
    tot_cost = 0.0
    for rec in _iter(path):
        if rec.get("ticket") != key:
            continue
        g = groups.setdefault(
            (rec.get("task"), rec.get("model")),
            {"task": rec.get("task"), "model": rec.get("model"),
             "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        g["input_tokens"] += rec.get("input_tokens") or 0
        g["output_tokens"] += rec.get("output_tokens") or 0
        g["cost_usd"] = round(g["cost_usd"] + (rec.get("cost_usd") or 0), 6)
        tot_in += rec.get("input_tokens") or 0
        tot_out += rec.get("output_tokens") or 0
        tot_cost += rec.get("cost_usd") or 0
    return {"tasks": list(groups.values()), "input_tokens": tot_in,
            "output_tokens": tot_out, "cost_usd": round(tot_cost, 6)}


def summary(path: Optional[str] = None) -> dict:
    path = path or LEDGER_PATH
    today = _now_iso()[:10]
    acc = {"today": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0},
           "allTime": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}}
    for rec in _iter(path):
        buckets = ["allTime"]
        if str(rec.get("ts", "")).startswith(today):
            buckets.append("today")
        for b in buckets:
            acc[b]["cost_usd"] += rec.get("cost_usd") or 0
            acc[b]["input_tokens"] += rec.get("input_tokens") or 0
            acc[b]["output_tokens"] += rec.get("output_tokens") or 0
    acc["today"]["cost_usd"] = round(acc["today"]["cost_usd"], 6)
    acc["allTime"]["cost_usd"] = round(acc["allTime"]["cost_usd"], 6)
    return acc
