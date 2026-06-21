# Per-Ticket Token & Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture per-call token usage + USD cost from every backend `claude -p` task (Council reviewers + FRIDAY chat) into a durable append-only ledger, aggregate it per ticket alongside existing OTEL evidence cost, and display it per-task/per-model in the UI.

**Architecture:** A new `backend/usage_ledger.py` owns a JSONL ledger and the pure parsers that pull `total_cost_usd` + `usage` out of the stream-json `result` event and `model` out of the `system` init event. `council.py` and `chat.py` import those parsers, accumulate usage during their existing read loops, and append one ledger line per completion. `server.py` exposes `/api/usage/ticket/{key}` (ledger + OTEL combined) and `/api/usage/summary`. The frontend adds a council-panel breakdown, a per-ticket breakdown component, a lane-card total badge, and a top-bar global total.

**Tech Stack:** Python 3.12 + FastAPI + pytest (backend); React + Vite + TypeScript (frontend, no unit-test harness).

## Global Constraints

- Run backend tests with **python3.12** (`python3.12 -m pytest`).
- **Model id is read from the stream's `system` init event** (`event["model"]`), not from config — this keeps tracking independent of the not-yet-built model-switching change. Fallback to `"default"` when absent.
- Ledger file path: `~/qa-dashboard/usage-ledger.jsonl`, overridable via env var **`SCRIBE_USAGE_LEDGER`**.
- **DRY:** the parsers (`parse_result_usage`, `parse_model_from_init`) live only in `usage_ledger.py`; `council.py` and `chat.py` import them.
- **Windows caveat:** async-subprocess tests (the council/chat integration tests) hit the pre-existing `WinError 6` on Windows (7 such tests already fail there). The pure-function tests in Task 1 + Task 3's `_synthesize` test are the cross-platform correctness guarantee; the subprocess integration tests are CI/Linux-verified.
- Frontend tasks have no unit-test harness → verify via `npm run build` (type-check) and a visual check, then commit.
- Chat has no ticket context (`POST /api/chat/send` carries none) → chat ledger records use `ticket=null` and roll into the global total only.

---

## File Structure

- **Create** `backend/usage_ledger.py` — ledger I/O + pure parsers + aggregation/summary.
- **Create** `backend/tests/test_usage_ledger.py` — unit tests for the above.
- **Create** `backend/tests/fixtures/stub_claude_pass_with_usage.sh` — stub emitting a `result` event with cost+usage.
- **Modify** `backend/council.py` — capture usage in `_run_reviewer`, propagate in `_synthesize`, append ledger lines in `_runner`.
- **Modify** `backend/chat.py` — capture usage in the `result` branch, append a ledger line.
- **Modify** `backend/server.py` — add `/api/usage/ticket/{key}` and `/api/usage/summary`.
- **Modify** `backend/tests/test_council.py` — `_synthesize` carries usage.
- **Modify** `backend/tests/test_council_integration.py` — `_run_reviewer` captures usage (subprocess).
- **Modify** `backend/tests/test_chat.py` — chat appends a ledger line (subprocess).
- **Create** `backend/tests/test_usage_api.py` — endpoint tests via TestClient.
- **Modify** `frontend/src/types.ts` — add usage types; extend `CouncilVerdict` reviewer shape.
- **Modify** `frontend/src/api.ts` — `getTicketUsage`, `getUsageSummary`.
- **Modify** `frontend/src/components/CouncilPanel.tsx` — per-reviewer model·tokens·$.
- **Create** `frontend/src/components/UsageBreakdown.tsx` — per-ticket task table (presentational).
- **Modify** `frontend/src/components/LaneCard.tsx` — total $ badge + embed `<UsageBreakdown>`.
- **Modify** `frontend/src/components/TopBar.tsx` — global spend figure.

---

### Task 1: Usage-ledger parsers, store, and aggregation (`usage_ledger.py`)

**Files:**
- Create: `backend/usage_ledger.py`
- Test: `backend/tests/test_usage_ledger.py`

**Interfaces:**
- Produces:
  - `parse_result_usage(event: dict) -> dict` → `{cost_usd, duration_ms, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` (all numeric, default 0).
  - `parse_model_from_init(event: dict) -> Optional[str]` → `event["model"]` for a `system`/`init` event, else `None`.
  - `record(*, task: str, ticket: Optional[str], pipeline_id: Optional[str], model: Optional[str], usage: dict, session_id: str = "", is_error: bool = False, ts: Optional[str] = None, path: Optional[str] = None) -> dict` → appends one JSON line, returns the record.
  - `aggregate_for_ticket(key: str, path: Optional[str] = None) -> dict` → `{"tasks": [{task, model, input_tokens, output_tokens, cost_usd}], "input_tokens": int, "output_tokens": int, "cost_usd": float}`.
  - `summary(path: Optional[str] = None) -> dict` → `{"today": {...}, "allTime": {...}}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_usage_ledger.py
import json
import os

import usage_ledger as ul


def test_parse_result_usage_pulls_cost_and_tokens():
    event = {
        "type": "result", "subtype": "success", "total_cost_usd": 0.0123,
        "duration_ms": 4200,
        "usage": {"input_tokens": 1200, "output_tokens": 340,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 7},
    }
    u = ul.parse_result_usage(event)
    assert u["cost_usd"] == 0.0123
    assert u["input_tokens"] == 1200
    assert u["output_tokens"] == 340
    assert u["cache_read_input_tokens"] == 7
    assert u["duration_ms"] == 4200


def test_parse_result_usage_defaults_to_zero_when_missing():
    u = ul.parse_result_usage({"type": "result"})
    assert u == {"cost_usd": 0.0, "duration_ms": 0, "input_tokens": 0,
                 "output_tokens": 0, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}


def test_parse_model_from_init():
    assert ul.parse_model_from_init(
        {"type": "system", "subtype": "init", "model": "claude-haiku-4-5"}
    ) == "claude-haiku-4-5"
    assert ul.parse_model_from_init({"type": "assistant"}) is None


def test_record_then_aggregate_groups_by_task_and_model(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    ul.record(task="qa-evidence", ticket="INV-1", pipeline_id="pl1",
              model="claude-haiku-4-5",
              usage={"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 20},
              path=p)
    ul.record(task="code-reviewer", ticket="INV-1", pipeline_id="pl1",
              model="default",
              usage={"cost_usd": 0.50, "input_tokens": 8000, "output_tokens": 900},
              path=p)
    ul.record(task="qa-evidence", ticket="INV-2", pipeline_id="pl2",
              model="claude-haiku-4-5",
              usage={"cost_usd": 0.02, "input_tokens": 200, "output_tokens": 30},
              path=p)

    agg = ul.aggregate_for_ticket("INV-1", path=p)
    assert agg["cost_usd"] == 0.51
    assert agg["input_tokens"] == 8100
    assert agg["output_tokens"] == 920
    tasks = {(t["task"], t["model"]): t for t in agg["tasks"]}
    assert tasks[("qa-evidence", "claude-haiku-4-5")]["cost_usd"] == 0.01
    assert tasks[("code-reviewer", "default")]["input_tokens"] == 8000


def test_aggregate_missing_file_is_empty(tmp_path):
    agg = ul.aggregate_for_ticket("NOPE", path=str(tmp_path / "absent.jsonl"))
    assert agg == {"tasks": [], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def test_record_writes_one_json_line(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    rec = ul.record(task="chat", ticket=None, pipeline_id=None, model=None,
                    usage={"cost_usd": 0.005, "input_tokens": 50, "output_tokens": 5},
                    session_id="s1", path=p)
    assert rec["model"] == "default"   # None coerced to "default"
    assert rec["ticket"] is None
    with open(p, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["task"] == "chat"


def test_summary_today_equals_alltime_when_all_today(tmp_path, monkeypatch):
    p = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(ul, "_now_iso", lambda: "2026-06-20T10:00:00Z")
    ul.record(task="chat", ticket=None, pipeline_id=None, model="default",
              usage={"cost_usd": 0.03, "input_tokens": 300, "output_tokens": 40},
              path=p)
    s = ul.summary(path=p)
    assert s["allTime"]["cost_usd"] == 0.03
    assert s["today"]["cost_usd"] == 0.03
    assert s["allTime"]["input_tokens"] == 300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3.12 -m pytest tests/test_usage_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'usage_ledger'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/usage_ledger.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3.12 -m pytest tests/test_usage_ledger.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/usage_ledger.py backend/tests/test_usage_ledger.py
git commit -m "feat(usage): add usage ledger module + stream-json usage parsers"
```

---

### Task 2: Council captures usage and writes ledger lines (`council.py`)

**Files:**
- Modify: `backend/council.py` (read loop ~92-115, outcome returns ~127-146, `_synthesize` ~189-226, `_runner` ~295-331)
- Modify: `backend/tests/test_council.py`
- Create: `backend/tests/fixtures/stub_claude_pass_with_usage.sh`
- Modify: `backend/tests/test_council_integration.py`

**Interfaces:**
- Consumes: `usage_ledger.parse_result_usage`, `usage_ledger.parse_model_from_init`, `usage_ledger.record` (Task 1).
- Produces: each reviewer outcome dict gains `"model": Optional[str]` and `"usage": dict`; each `_synthesize` `reviewers` entry gains `"model"` and `"usage"`; the persisted `councilPayload` gains `"cost_usd": float`.

- [ ] **Step 1: Write the failing pure test (`_synthesize` propagation)**

```python
# add to backend/tests/test_council.py
def test_synthesize_carries_per_reviewer_usage():
    outcomes = [
        {"name": "qa-evidence", "verdict": "PASS", "reason": "", "error": None,
         "model": "claude-haiku-4-5",
         "usage": {"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 20}},
        {"name": "code-reviewer", "verdict": "PASS", "reason": "", "error": None,
         "model": "default",
         "usage": {"cost_usd": 0.50, "input_tokens": 8000, "output_tokens": 900}},
    ]
    result = _synthesize(outcomes)
    by_name = {r["name"]: r for r in result["reviewers"]}
    assert by_name["qa-evidence"]["model"] == "claude-haiku-4-5"
    assert by_name["code-reviewer"]["usage"]["cost_usd"] == 0.50
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && python3.12 -m pytest tests/test_council.py::test_synthesize_carries_per_reviewer_usage -v`
Expected: FAIL — `KeyError: 'model'` (reviewers entries don't carry it yet).

- [ ] **Step 3: Implement — import, capture loop, outcome, synthesize, ledger write**

In `backend/council.py` add the import near the top (after `import threading`):

```python
import usage_ledger
```

Replace the read-loop body (currently the single `if event.get("type") == "assistant":` block, ~lines 110-115) so it also captures model + result usage. First add two locals before the `while True:` loop (next to `killed = False`):

```python
    reviewer_model: Optional[str] = None
    reviewer_usage: dict = {}
```

Then the event dispatch inside the loop becomes:

```python
            if event.get("type") == "system":
                reviewer_model = usage_ledger.parse_model_from_init(event) or reviewer_model
            elif event.get("type") == "assistant":
                transcript_chunks.append(_extract_text_from_assistant(event.get("message", {})))
            elif event.get("type") == "result":
                reviewer_usage = usage_ledger.parse_result_usage(event)
```

Add `model`/`usage` to all three outcome returns. The success return (~lines 140-146) becomes:

```python
    verdict, reason = _parse_verdict_line(stdout)
    return {
        "name": reviewer.name,
        "verdict": verdict,
        "reason": reason,
        "stdout": stdout,
        "error": None,
        "model": reviewer_model,
        "usage": reviewer_usage,
    }
```

And add `"model": reviewer_model, "usage": reviewer_usage,` to the two ERROR returns (~lines 127 and 131-137) as well.

In `_synthesize`, every `reviewers_summary.append({...})` call gains the two fields. Each becomes, e.g.:

```python
        if verdict == "PASS":
            reviewers_summary.append({"name": name, "verdict": "PASS", "reason": reason,
                                      "model": o.get("model"), "usage": o.get("usage") or {}})
            continue
```

Apply the same `"model": o.get("model"), "usage": o.get("usage") or {}` addition to the BLOCK, ERROR, and UNPARSEABLE appends.

In `_runner` (in `start`), right after `verdict = _synthesize(outcomes)` (~line 319), add the ledger writes and a payload cost total:

```python
            verdict = _synthesize(outcomes)
            for o in outcomes:
                usage_ledger.record(
                    task=o["name"], ticket=ticket_key, pipeline_id=pipeline_id,
                    model=o.get("model"), usage=o.get("usage") or {},
                    is_error=(o.get("verdict") == "ERROR"),
                )
            verdict["cost_usd"] = round(
                sum((o.get("usage") or {}).get("cost_usd", 0) for o in outcomes), 6
            )
```

- [ ] **Step 4: Run the pure test to verify it passes**

Run: `cd backend && python3.12 -m pytest tests/test_council.py -v`
Expected: PASS (all council unit tests, including the new one).

- [ ] **Step 5: Add the subprocess stub + integration test (CI/Linux-verified)**

Create `backend/tests/fixtures/stub_claude_pass_with_usage.sh`:

```bash
#!/bin/bash
# Stub: stream-json transcript with a result event carrying cost + usage.
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"stub-session","model":"claude-haiku-4-5"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Checking...\n\nVERDICT: PASS"}]}}
{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.0123,"duration_ms":4200,"session_id":"stub-session","usage":{"input_tokens":1200,"output_tokens":340,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}
EOF
exit 0
```

Add to `backend/tests/test_council_integration.py`:

```python
import os
import pytest
from council import _run_reviewer, Reviewer
from council_prompts import build_qa_evidence_prompt

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.asyncio
async def test_run_reviewer_captures_usage_and_model(monkeypatch):
    # NOTE: spawns a subprocess; skips cleanly on Windows (pre-existing WinError 6).
    stub = os.path.join(FIXTURES_DIR, "stub_claude_pass_with_usage.sh")
    monkeypatch.setenv("CLAUDE_BIN", stub)
    rv = Reviewer(name="qa-evidence", prompt_builder=build_qa_evidence_prompt)
    outcome = await _run_reviewer(rv, {"ticket_key": "INV-1", "run_name": "r1"})
    assert outcome["verdict"] == "PASS"
    assert outcome["model"] == "claude-haiku-4-5"
    assert outcome["usage"]["cost_usd"] == 0.0123
    assert outcome["usage"]["input_tokens"] == 1200
```

- [ ] **Step 6: Run the integration test (Linux/CI) + full council suite**

Run: `cd backend && python3.12 -m pytest tests/test_council.py tests/test_council_integration.py -v`
Expected: PASS on Linux/CI. On Windows the new subprocess test may error with `WinError 6` (pre-existing, documented) — the pure `_synthesize` test still passes.

- [ ] **Step 7: Commit**

```bash
git add backend/council.py backend/tests/test_council.py backend/tests/test_council_integration.py backend/tests/fixtures/stub_claude_pass_with_usage.sh
git commit -m "feat(usage): council captures token+cost usage and writes ledger lines"
```

---

### Task 3: Chat captures usage and writes a ledger line (`chat.py`)

**Files:**
- Modify: `backend/chat.py` (result branch ~154-162; loop ~110-152)
- Modify: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: `usage_ledger.parse_result_usage`, `usage_ledger.parse_model_from_init`, `usage_ledger.record` (Task 1).
- Produces: one `task="chat"`, `ticket=None` ledger line per completed turn; the yielded `result` dict gains `input_tokens`/`output_tokens`.

- [ ] **Step 1: Write the failing test (subprocess stub; CI/Linux-verified)**

```python
# add to backend/tests/test_chat.py
import json as _json
import os as _os


@pytest.mark.asyncio
async def test_chat_writes_usage_ledger_line(monkeypatch, tmp_path):
    # NOTE: spawns a subprocess; skips cleanly on Windows (pre-existing WinError 6).
    import usage_ledger
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    # Stub shell that emits an init (model) + a result event with usage.
    line = (
        '{"type":"system","subtype":"init","session_id":"s1","model":"claude-haiku-4-5"}\n'
        '{"type":"result","subtype":"success","is_error":false,"session_id":"s1",'
        '"total_cost_usd":0.007,"duration_ms":900,'
        '"usage":{"input_tokens":40,"output_tokens":8}}'
    )
    monkeypatch.setattr(chat, "_build_cmd",
                        lambda message, sid: f"printf '%s' {chat.shlex.quote(line)}")
    monkeypatch.setattr(chat, "_session_exists", lambda _sid: False)

    events = [evt async for evt in chat.chat_stream("hello")]

    assert any(e["type"] == "result" for e in events)
    with open(ledger, encoding="utf-8") as f:
        recs = [_json.loads(l) for l in f if l.strip()]
    assert len(recs) == 1
    assert recs[0]["task"] == "chat"
    assert recs[0]["ticket"] is None
    assert recs[0]["model"] == "claude-haiku-4-5"
    assert recs[0]["cost_usd"] == 0.007
    assert recs[0]["input_tokens"] == 40
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && python3.12 -m pytest tests/test_chat.py::test_chat_writes_usage_ledger_line -v`
Expected: FAIL — no ledger file written (chat doesn't record yet). (On Windows: may error with `WinError 6`; verify on Linux/CI.)

- [ ] **Step 3: Implement**

In `backend/chat.py` add the import near the top (after `import shlex`):

```python
import usage_ledger
```

Add a model local at the start of the streaming loop (near `start = asyncio.get_event_loop().time()`, before the read `while`):

```python
    chat_model: Optional[str] = None
```

Capture the model when the `system` init event arrives. The loop already branches on `etype`; add a branch (alongside the existing `elif etype == "result":`):

```python
            elif etype == "system":
                chat_model = usage_ledger.parse_model_from_init(event) or chat_model
```

Replace the `result` branch (~lines 154-162) with one that records usage and includes tokens in the yielded event:

```python
            elif etype == "result":
                u = usage_ledger.parse_result_usage(event)
                usage_ledger.record(
                    task="chat", ticket=None, pipeline_id=None, model=chat_model,
                    usage=u, session_id=event.get("session_id", ""),
                    is_error=bool(event.get("is_error")),
                )
                yield {
                    "type": "result",
                    "session_id": event.get("session_id", ""),
                    "cost": u["cost_usd"],
                    "input_tokens": u["input_tokens"],
                    "output_tokens": u["output_tokens"],
                    "duration_ms": u["duration_ms"],
                    "is_error": bool(event.get("is_error")),
                    "result": event.get("result", ""),
                }
                # result is the terminal event for this turn
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python3.12 -m pytest tests/test_chat.py -v`
Expected: PASS on Linux/CI (existing chat timeout tests still pass; the new one writes the ledger line).

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py backend/tests/test_chat.py
git commit -m "feat(usage): chat records token+cost usage to the ledger"
```

---

### Task 4: Usage API endpoints (`server.py`)

**Files:**
- Modify: `backend/server.py` (add routes near the OTEL endpoints, ~after line 440)
- Create: `backend/tests/test_usage_api.py`

**Interfaces:**
- Consumes: `usage_ledger.aggregate_for_ticket`, `usage_ledger.summary` (Task 1); existing `_otel.total_cost_for_ticket`; existing `EVIDENCE_DIR`.
- Produces:
  - `GET /api/usage/ticket/{key}` → `{ticket, tasks:[{task, model, input_tokens, output_tokens, cost_usd}], total_cost_usd, total_input_tokens, total_output_tokens}` (an `evidence-runs` task row with null tokens is appended when OTEL has cost).
  - `GET /api/usage/summary` → `{today:{cost_usd,input_tokens,output_tokens}, allTime:{...}}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_usage_api.py
import usage_ledger
from fastapi.testclient import TestClient
import server


def test_usage_ticket_combines_ledger_and_otel(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    # No OTEL cost for this ticket in the test env.
    monkeypatch.setattr(server._otel, "total_cost_for_ticket", lambda *_a, **_k: None)
    usage_ledger.record(task="code-reviewer", ticket="INV-9", pipeline_id="p",
                        model="default",
                        usage={"cost_usd": 0.5, "input_tokens": 8000, "output_tokens": 900},
                        path=ledger)

    client = TestClient(server.app)
    r = client.get("/api/usage/ticket/INV-9")
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"] == "INV-9"
    assert body["total_cost_usd"] == 0.5
    assert body["total_input_tokens"] == 8000
    assert any(t["task"] == "code-reviewer" for t in body["tasks"])


def test_usage_ticket_appends_evidence_runs_row(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    monkeypatch.setattr(server._otel, "total_cost_for_ticket", lambda *_a, **_k: 1.25)
    client = TestClient(server.app)
    body = client.get("/api/usage/ticket/INV-X").json()
    ev = [t for t in body["tasks"] if t["task"] == "evidence-runs"][0]
    assert ev["cost_usd"] == 1.25
    assert ev["input_tokens"] is None
    assert body["total_cost_usd"] == 1.25


def test_usage_summary_shape(tmp_path, monkeypatch):
    ledger = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    client = TestClient(server.app)
    body = client.get("/api/usage/summary").json()
    assert set(body.keys()) == {"today", "allTime"}
    assert "cost_usd" in body["allTime"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && python3.12 -m pytest tests/test_usage_api.py -v`
Expected: FAIL — `404` for the new routes (not defined yet).

- [ ] **Step 3: Implement the endpoints**

In `backend/server.py` add the import near the other module imports (next to `import otel as _otel`):

```python
import usage_ledger
```

Add the routes after the OTEL section (after `api_otel_status`, ~line 440):

```python
@app.get("/api/usage/ticket/{key}")
async def api_usage_ticket(key: str):
    """Per-ticket token + USD breakdown: ledger (council + chat) plus OTEL evidence $."""
    agg = usage_ledger.aggregate_for_ticket(key)
    tasks = list(agg["tasks"])
    runs_path = os.path.join(EVIDENCE_DIR, key, "runs")
    ev_cost = _otel.total_cost_for_ticket(runs_path)
    if ev_cost:
        tasks.append({"task": "evidence-runs", "model": None,
                      "input_tokens": None, "output_tokens": None, "cost_usd": ev_cost})
    return {
        "ticket": key,
        "tasks": tasks,
        "total_cost_usd": round(agg["cost_usd"] + (ev_cost or 0), 6),
        "total_input_tokens": agg["input_tokens"],
        "total_output_tokens": agg["output_tokens"],
    }


@app.get("/api/usage/summary")
async def api_usage_summary():
    """Global spend totals (today / all-time) for the dashboard."""
    return usage_ledger.summary()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd backend && python3.12 -m pytest tests/test_usage_api.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/server.py backend/tests/test_usage_api.py
git commit -m "feat(usage): add /api/usage/ticket and /api/usage/summary endpoints"
```

---

### Task 5: Frontend types + API client (`types.ts`, `api.ts`)

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `TaskUsage`, `TicketUsage`, `UsageSummary` types; `getTicketUsage(key): Promise<TicketUsage>`, `getUsageSummary(): Promise<UsageSummary>`; extends the `CouncilVerdict` reviewer shape with optional `model` + `usage`.

- [ ] **Step 1: Add types to `frontend/src/types.ts`**

Append:

```typescript
export interface TaskUsage {
  task: string
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number
}

export interface TicketUsage {
  ticket: string
  tasks: TaskUsage[]
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
}

export interface UsageBucket {
  cost_usd: number
  input_tokens: number
  output_tokens: number
}

export interface UsageSummary {
  today: UsageBucket
  allTime: UsageBucket
}
```

Find the `CouncilVerdict` interface's `reviewers` array element type and add two optional fields to that element: `model?: string | null` and `usage?: { cost_usd?: number; input_tokens?: number; output_tokens?: number }`.

- [ ] **Step 2: Add client functions to `frontend/src/api.ts`**

Add `TicketUsage, UsageSummary` to the existing top `import { ... } from './types'`, then append:

```typescript
export async function getTicketUsage(key: string): Promise<TicketUsage> {
  const res = await fetch(`${BASE}/usage/ticket/${key}`)
  if (!res.ok) throw new Error(`getTicketUsage failed: ${res.status}`)
  return res.json()
}

export async function getUsageSummary(): Promise<UsageSummary> {
  const res = await fetch(`${BASE}/usage/summary`)
  if (!res.ok) throw new Error(`getUsageSummary failed: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TS errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(usage): frontend usage types + API client"
```

---

### Task 6: Council panel per-reviewer breakdown (`CouncilPanel.tsx`)

**Files:**
- Modify: `frontend/src/components/CouncilPanel.tsx` (reviewers `.map`, ~lines 37-42)

**Interfaces:**
- Consumes: `CouncilVerdict.reviewers[].model` + `.usage` (Task 5), already present on `verdict` prop.

- [ ] **Step 1: Render model + tokens + $ per reviewer**

Inside the `reviewers.map(r => ( ... ))` `<li>`, after the existing `{r.reason && ...}` line, add:

```tsx
            {r.usage && (r.usage.cost_usd != null || r.usage.input_tokens != null) && (
              <span className="reviewer-usage" style={{ color: 'var(--text-dim)', fontSize: 10, marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>
                {r.model ?? 'default'} · {(r.usage.input_tokens ?? 0)}/{(r.usage.output_tokens ?? 0)} tok · ${(r.usage.cost_usd ?? 0).toFixed(4)}
              </span>
            )}
```

- [ ] **Step 2: Type-check + visual verify**

Run: `cd frontend && npm run build`
Expected: build succeeds. Then in the running dashboard open a ticket whose council has finished; each reviewer row shows `model · in/out tok · $cost`, and the Code Reviewer's cost is now visible.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CouncilPanel.tsx
git commit -m "feat(usage): show per-reviewer model/tokens/cost in council panel"
```

---

### Task 7: Per-ticket breakdown component (`UsageBreakdown.tsx`)

**Files:**
- Create: `frontend/src/components/UsageBreakdown.tsx`

**Interfaces:**
- Consumes: `TicketUsage` (Task 5) as a prop (presentational; the parent fetches).
- Produces: `<UsageBreakdown usage={...} />` — a table of task · model · tokens · $ + a total row.

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/UsageBreakdown.tsx
import { TicketUsage } from '../types'

export function UsageBreakdown({ usage }: { usage: TicketUsage }) {
  if (!usage || usage.tasks.length === 0) {
    return <div className="usage-breakdown usage-breakdown--empty">No AI spend recorded yet.</div>
  }
  return (
    <table className="usage-breakdown" style={{ width: '100%', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
          <th>Task</th><th>Model</th><th style={{ textAlign: 'right' }}>In/Out tok</th><th style={{ textAlign: 'right' }}>Cost</th>
        </tr>
      </thead>
      <tbody>
        {usage.tasks.map((t, i) => (
          <tr key={`${t.task}-${t.model}-${i}`}>
            <td>{t.task}</td>
            <td>{t.model ?? '—'}</td>
            <td style={{ textAlign: 'right' }}>
              {t.input_tokens == null ? '—' : `${t.input_tokens}/${t.output_tokens}`}
            </td>
            <td style={{ textAlign: 'right' }}>${t.cost_usd.toFixed(4)}</td>
          </tr>
        ))}
        <tr style={{ fontWeight: 600, borderTop: '1px solid var(--border)' }}>
          <td colSpan={2}>Total</td>
          <td style={{ textAlign: 'right' }}>{usage.total_input_tokens}/{usage.total_output_tokens}</td>
          <td style={{ textAlign: 'right' }}>${usage.total_cost_usd.toFixed(4)}</td>
        </tr>
      </tbody>
    </table>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds (component compiles even though not yet mounted).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/UsageBreakdown.tsx
git commit -m "feat(usage): per-ticket usage breakdown component"
```

---

### Task 8: Lane-card total badge + embed breakdown (`LaneCard.tsx`)

**Files:**
- Modify: `frontend/src/components/LaneCard.tsx` (imports; header ~58-90; body)

**Interfaces:**
- Consumes: `getTicketUsage` (Task 5), `UsageBreakdown` (Task 7), `TicketUsage` (Task 5). `lane.ticket?.key` (or the lane's ticket key — match the existing field used by `lane-card__key`).

- [ ] **Step 1: Fetch usage for the lane and show the total in the header**

At the top of `LaneCard.tsx` add imports:

```tsx
import { useEffect, useState } from 'react'
import { getTicketUsage } from '../api'
import { TicketUsage } from '../types'
import { UsageBreakdown } from './UsageBreakdown'
```

(If `useEffect`/`useState` are already imported, merge rather than duplicate.)

Inside the `LaneCard` component body, before `return (`, add (use the same key expression the component already uses to render `lane-card__key`):

```tsx
  const ticketKey = lane.ticket.key  // match the existing key field used in the header
  const [usage, setUsage] = useState<TicketUsage | null>(null)
  useEffect(() => {
    let alive = true
    getTicketUsage(ticketKey).then(u => { if (alive) setUsage(u) }).catch(() => {})
    return () => { alive = false }
  }, [ticketKey])
```

In the `lane-card__header` block, add a badge (after the `lane-card__key` element):

```tsx
        {usage && usage.total_cost_usd > 0 && (
          <span className="lane-card__cost" title="AI spend on this ticket (council + chat + evidence)"
                style={{ marginLeft: 'auto', color: 'var(--text-dim)', fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>
            ${usage.total_cost_usd.toFixed(2)}
          </span>
        )}
```

- [ ] **Step 2: Embed the breakdown in the card body**

After the `lane-card__progress` block, add:

```tsx
      {usage && usage.tasks.length > 0 && (
        <details className="lane-card__usage">
          <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--text-dim)' }}>AI usage</summary>
          <UsageBreakdown usage={usage} />
        </details>
      )}
```

- [ ] **Step 3: Type-check + visual verify**

Run: `cd frontend && npm run build`
Expected: build succeeds. In the dashboard, a lane with recorded spend shows a `$X.XX` badge in its header; expanding "AI usage" shows the per-task/per-model table.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/LaneCard.tsx
git commit -m "feat(usage): lane-card cost badge + expandable per-ticket breakdown"
```

---

### Task 9: Global spend total in the top bar (`TopBar.tsx`)

**Files:**
- Modify: `frontend/src/components/TopBar.tsx` (imports; effect; `top-bar__title` or `top-bar__actions`, ~63-86)

**Interfaces:**
- Consumes: `getUsageSummary` (Task 5), `UsageSummary` (Task 5).

- [ ] **Step 1: Fetch the summary and render it**

Add imports at the top of `TopBar.tsx`:

```tsx
import { getUsageSummary } from '../api'
import { UsageSummary } from '../types'
```

(Merge `useEffect`/`useState` into the existing React import if needed.)

In the component body, before `return (`, add:

```tsx
  const [spend, setSpend] = useState<UsageSummary | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => getUsageSummary().then(s => { if (alive) setSpend(s) }).catch(() => {})
    load()
    const handle = setInterval(load, 30000)
    return () => { alive = false; clearInterval(handle) }
  }, [])
```

Inside `top-bar__actions` (~line 86), add a figure:

```tsx
        {spend && (
          <span className="top-bar__spend" title="AI spend — today / all-time"
                style={{ fontSize: 12, color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>
            ${spend.today.cost_usd.toFixed(2)} today · ${spend.allTime.cost_usd.toFixed(2)} all-time
          </span>
        )}
```

- [ ] **Step 2: Type-check + visual verify**

Run: `cd frontend && npm run build`
Expected: build succeeds. The top bar shows `$X today · $Y all-time`, refreshing every 30s.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TopBar.tsx
git commit -m "feat(usage): global AI spend total in the top bar"
```

---

## Self-Review

**1. Spec coverage:**
- Per-task + per-model granularity → ledger `(task, model)` grouping (Task 1) + breakdown UI (Tasks 6-8). ✓
- Append-only JSONL ledger → Task 1 (`usage_ledger.py`, `SCRIBE_USAGE_LEDGER`). ✓
- Capture Council + Chat with tokens + model → Tasks 2-3. ✓
- Per-ticket total = ledger + OTEL evidence $ → Task 4 (`/api/usage/ticket`). ✓
- Four display surfaces: Council panel (Task 6), per-ticket breakdown (Tasks 7-8), lane-card badge (Task 8), global total (Task 9). ✓
- Chat `ticket=null`, evidence tokens null → Task 3 + Task 4 (`evidence-runs` row with null tokens). ✓
- Model read from stream init event (order-independent vs model-switching) → Task 1 parser + Tasks 2-3 wiring. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; tests are concrete. The only intentional "match the existing field" notes (LaneCard `lane.ticket.key`, CouncilVerdict reviewer element) are flagged because they depend on the current shape of code the executor is editing — they are explicit instructions, not deferred work.

**3. Type consistency:** `parse_result_usage`/`parse_model_from_init`/`record`/`aggregate_for_ticket`/`summary` signatures match across Tasks 1-4. `TicketUsage`/`TaskUsage`/`UsageSummary` defined in Task 5 are consumed unchanged in Tasks 6-9. Ledger record keys (`cost_usd`, `input_tokens`, `output_tokens`, `model`, `task`, `ticket`) are identical in writer (Task 1), council/chat writers (Tasks 2-3), aggregator (Task 1), and API (Task 4).

---

## Execution notes

- **Order matters:** Task 1 → 2/3/4 (backend) must precede 5 → 6/7/8/9 (frontend, which consume the API). Tasks 2 and 3 are independent of each other; 6, 7, 9 are independent; 8 depends on 7.
- **Commit, don't push.** Per the user's instruction, do not commit to `main` or push without asking — the two design specs and these commits should land on a branch (`feat/usage-tracking`) when the user is ready to look at results.
