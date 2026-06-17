"""
Read local Claude Code OTEL telemetry and compute per-run cost.

The OTEL collector writes ~/.otel-collector/claude-code.jsonl.
Each line is a JSON object with a resourceLogs array.
We extract api_request events, filter by session ID and time window,
and sum cost_usd to get the Claude spend for a given evidence run.
"""

import json
import os
from typing import Optional

JSONL_PATH = os.path.expanduser("~/.otel-collector/claude-code.jsonl")


def _iter_api_requests(jsonl_path: str):
    """Yield (session_id, timestamp_iso, cost_usd) for every api_request event."""
    if not os.path.exists(jsonl_path):
        return
    with open(jsonl_path, "r", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for rl in obj.get("resourceLogs", []):
                for sl in rl.get("scopeLogs", []):
                    for rec in sl.get("logRecords", []):
                        body = rec.get("body", {})
                        if isinstance(body, dict):
                            body_str = body.get("stringValue", "")
                        else:
                            body_str = str(body)
                        if "api_request" not in body_str:
                            continue
                        attrs = {a["key"]: a["value"] for a in rec.get("attributes", [])}
                        session_id = (attrs.get("session.id") or {}).get("stringValue", "")
                        ts = (attrs.get("event.timestamp") or {}).get("stringValue", "")
                        cost_val = attrs.get("cost_usd") or {}
                        cost = cost_val.get("doubleValue") or cost_val.get("intValue") or 0.0
                        if session_id and ts:
                            yield session_id, ts, float(cost)


def cost_for_run(
    session_id: str,
    run_start_iso: str,
    run_end_iso: Optional[str],
    jsonl_path: str = JSONL_PATH,
) -> float:
    """Sum cost_usd for api_request events matching session_id within the time window."""
    if not session_id:
        return 0.0
    total = 0.0
    for sid, ts, cost in _iter_api_requests(jsonl_path):
        if sid != session_id:
            continue
        if run_start_iso and ts < run_start_iso:
            continue
        if run_end_iso and ts > run_end_iso:
            continue
        total += cost
    return round(total, 4)


def costs_for_ticket(ticket_runs_path: str, jsonl_path: str = JSONL_PATH) -> dict:
    """
    Walk all runs under a ticket's runs/ dir and return a mapping of
    run_name -> cost_usd by reading each run's infra.json for session context.

    Returns {} if no runs have session tracking data.
    """
    if not os.path.isdir(ticket_runs_path):
        return {}
    result = {}
    for run_name in os.listdir(ticket_runs_path):
        run_path = os.path.join(ticket_runs_path, run_name)
        if not os.path.isdir(run_path):
            continue
        infra_path = os.path.join(run_path, "infra.json")
        if not os.path.exists(infra_path):
            continue
        try:
            with open(infra_path) as f:
                infra = json.load(f)
        except Exception:
            continue
        session_id = infra.get("claude_session_id", "")
        run_start = infra.get("started", "")
        run_end = infra.get("completed", "")
        if not session_id:
            continue
        cost = cost_for_run(session_id, run_start, run_end, jsonl_path)
        if cost > 0:
            result[run_name] = cost
    return result


def total_cost_for_ticket(ticket_runs_path: str, jsonl_path: str = JSONL_PATH) -> Optional[float]:
    """Sum cost across all runs for a ticket. Returns None if no OTEL data available."""
    costs = costs_for_ticket(ticket_runs_path, jsonl_path)
    if not costs:
        return None
    return round(sum(costs.values()), 4)


def is_available(jsonl_path: str = JSONL_PATH) -> bool:
    return os.path.exists(jsonl_path)
