"""Council Review Gate — orchestrates reviewer subagents at the Scribe step.

See ~/docs/superpowers/specs/2026-06-07-council-review-gate-design.md for the full design.

This module owns:
  - Spawning `claude -p` subprocesses per reviewer in parallel.
  - Parsing the final VERDICT: line from each reviewer's output.
  - Synthesizing reviewer verdicts into a final council verdict.
  - Emitting events on the Stream and persisting the verdict to PipelineStore.
  - Appending verdicts and overrides to ~/qa-dashboard/council-audit.jsonl.

The subprocess pattern is intentionally a near-clone of `chat.py` — that
module solved idle timeout, total timeout, and hang-killing, and we want
the same guarantees here.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import usage_ledger


COUNCIL_AUDIT_PATH = os.path.expanduser("~/qa-dashboard/council-audit.jsonl")
_AUDIT_LOCK = threading.Lock()

CLAUDE_BIN_ENV = "CLAUDE_BIN"
DEFAULT_IDLE_TIMEOUT_S = 120
DEFAULT_TOTAL_TIMEOUT_S = 300


@dataclass
class Reviewer:
    name: str
    prompt_builder: Callable[..., str]
    idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S
    total_timeout_s: int = DEFAULT_TOTAL_TIMEOUT_S
    model: Optional[str] = None


def _claude_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV, "claude")


def _build_reviewer_cmd(prompt: str, model: Optional[str] = None) -> list[str]:
    """Argv list for create_subprocess_exec. A `--model` flag is added only when
    `model` is set; the prompt is always the final element."""
    cmd = [
        _claude_bin(),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    return cmd


def _extract_text_from_assistant(message: dict) -> str:
    content = message.get("content") or []
    out = []
    for block in content:
        if block.get("type") == "text":
            out.append(block.get("text", ""))
    return "".join(out)


async def _run_reviewer(reviewer: Reviewer, ctx: dict) -> dict:
    """Spawn one reviewer subprocess and return its outcome.

    Outcome dict: {name, verdict, reason, stdout, error}.
    verdict is "PASS" | "BLOCK" | None (no verdict line) | "ERROR".
    """
    prompt = reviewer.prompt_builder(**ctx)
    cmd = _build_reviewer_cmd(prompt, reviewer.model)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    transcript_chunks: list[str] = []
    start = asyncio.get_event_loop().time()
    killed = False
    error: Optional[str] = None
    reviewer_model: Optional[str] = None
    reviewer_usage: dict = {}

    try:
        while True:
            if asyncio.get_event_loop().time() - start > reviewer.total_timeout_s:
                error = f"reviewer exceeded {reviewer.total_timeout_s}s wall-clock"
                killed = True
                break
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=reviewer.idle_timeout_s
                )
            except asyncio.TimeoutError:
                error = f"reviewer hung (no output for {reviewer.idle_timeout_s}s)"
                killed = True
                break
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "system":
                reviewer_model = usage_ledger.parse_model_from_init(event) or reviewer_model
            elif event.get("type") == "assistant":
                transcript_chunks.append(_extract_text_from_assistant(event.get("message", {})))
            elif event.get("type") == "result":
                reviewer_usage = usage_ledger.parse_result_usage(event)
    finally:
        if killed:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()

    stdout = "\n".join(transcript_chunks)

    if error:
        return {"name": reviewer.name, "verdict": "ERROR", "reason": "", "stdout": stdout, "error": error,
                "model": reviewer_model, "usage": reviewer_usage}

    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
        return {
            "name": reviewer.name,
            "verdict": "ERROR",
            "reason": "",
            "stdout": stdout,
            "error": f"reviewer crashed: exit {proc.returncode}: {stderr[-200:]}",
            "model": reviewer_model,
            "usage": reviewer_usage,
        }

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


_VERDICT_RE = re.compile(r"^VERDICT:[ \t]*(PASS|BLOCK)(?:[ \t]+(.*))?$", re.MULTILINE)


def _parse_verdict_line(text: str) -> tuple[Optional[str], str]:
    """Find the last `VERDICT: PASS|BLOCK [reason]` line in text.

    Returns (verdict, reason). If no valid verdict line is found, returns
    (None, ""). Reason is stripped of surrounding whitespace.

    Spec: the verdict label is case-sensitive ("exactly VERDICT:") so we
    don't normalize case here.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_VERDICT_RE.finditer(text))
    if not matches:
        return (None, "")
    last = matches[-1]
    verdict = last.group(1)
    reason = (last.group(2) or "").strip()
    return (verdict, reason)


def _synthesize(outcomes: list[dict]) -> dict:
    """Combine reviewer outcomes into a final council verdict.

    Rules (intentionally boring):
      - PASS only if EVERY reviewer's verdict == "PASS".
      - Any "BLOCK" → council BLOCK with that reviewer's reason surfaced.
      - Any None or unrecognized verdict → BLOCK "<name>: did not return a verdict".
      - Any "ERROR" → BLOCK "<name>: <error>".
      - Empty outcomes → BLOCK (fail-closed). The gate is hard, so the
        absence of evidence is not evidence of approval.
    """
    if not outcomes:
        return {
            "verdict": "BLOCK",
            "rationale": "no reviewers ran",
            "reviewers": [],
        }

    reviewers_summary = []
    block_reasons = []

    for o in outcomes:
        name = o["name"]
        verdict = o.get("verdict")
        reason = o.get("reason") or ""
        error = o.get("error") or ""

        if verdict == "PASS":
            reviewers_summary.append({"name": name, "verdict": "PASS", "reason": reason,
                                      "model": o.get("model"), "usage": o.get("usage") or {}})
            continue

        if verdict == "BLOCK":
            block_reasons.append(f"{name}: {reason or '(no reason given)'}")
            reviewers_summary.append({"name": name, "verdict": "BLOCK", "reason": reason,
                                      "model": o.get("model"), "usage": o.get("usage") or {}})
            continue

        if verdict == "ERROR":
            block_reasons.append(f"{name}: {error or '(no error message)'}")
            reviewers_summary.append({"name": name, "verdict": "ERROR", "reason": error,
                                      "model": o.get("model"), "usage": o.get("usage") or {}})
            continue

        block_reasons.append(f"{name}: did not return a verdict")
        reviewers_summary.append({"name": name, "verdict": "UNPARSEABLE", "reason": "",
                                  "model": o.get("model"), "usage": o.get("usage") or {}})

    if not block_reasons:
        return {
            "verdict": "PASS",
            "rationale": "All reviewers passed.",
            "reviewers": reviewers_summary,
        }

    return {
        "verdict": "BLOCK",
        "rationale": "; ".join(block_reasons),
        "reviewers": reviewers_summary,
    }


_streams = None
_pipeline_store = None


def configure(streams_registry, pipeline_store_instance):
    """Called once by server.py to inject dependencies (avoids circular imports)."""
    global _streams, _pipeline_store
    _streams = streams_registry
    _pipeline_store = pipeline_store_instance


def _default_reviewers() -> list:
    from council_prompts import build_qa_evidence_prompt, build_code_reviewer_prompt
    from config import QA_EVIDENCE_MODEL
    return [
        Reviewer(name="qa-evidence", prompt_builder=build_qa_evidence_prompt,
                 model=QA_EVIDENCE_MODEL),
        Reviewer(name="code-reviewer", prompt_builder=build_code_reviewer_prompt),
        # code-reviewer: model stays None → no --model flag → CLI default
    ]


async def _fetch_diffs(pr_refs: list) -> dict:
    """Fetch diffs from Bitbucket for each PR. Failures degrade to '(unavailable)'."""
    from bitbucket_client import get_pr_diff
    diffs: dict = {}
    for pr in pr_refs:
        repo = pr.get("repo", "")
        pr_id = pr.get("pr_id", "")
        if not repo or not pr_id:
            continue
        try:
            text = await get_pr_diff(repo, pr_id)
        except Exception as e:
            text = f"(diff fetch failed: {e})"
        diffs[f"{repo}/{pr_id}"] = text or "(diff empty)"
    return diffs


async def _run_reviewer_and_emit(reviewer: Reviewer, ctx: dict, stream) -> dict:
    """Run one reviewer, emit a reviewer_done event, return its outcome."""
    stream.append({"type": "reviewer_started", "reviewer": reviewer.name})
    outcome = await _run_reviewer(reviewer, ctx)
    stream.append({
        "type": "reviewer_done",
        "reviewer": reviewer.name,
        "verdict": outcome["verdict"],
        "reason": outcome["reason"] or outcome.get("error") or "",
    })
    return outcome


def start(
    ticket_key: str,
    run_name: str,
    pipeline_id: str,
    pr_refs: list,
) -> str:
    """Kick off the council. Returns councilStreamId. Non-blocking — the
    reviewer subprocesses run in a background task.
    """
    assert _streams is not None and _pipeline_store is not None, "council.configure() not called"

    import uuid
    council_stream_id = str(uuid.uuid4())
    stream = _streams.create(council_stream_id)

    _pipeline_store.upsert(pipeline_id, {"councilStatus": "pending", "councilPayload": None})

    async def _runner():
        try:
            stream.append({"type": "council_started", "ticket_key": ticket_key, "run_name": run_name})
            diffs = await _fetch_diffs(pr_refs)
            try:
                from jira_client import get_ticket_text
                ticket_text = await get_ticket_text(ticket_key)
            except Exception:
                ticket_text = ""
            ctx_qa = {"ticket_key": ticket_key, "run_name": run_name}
            ctx_cr = {
                "ticket_key": ticket_key,
                "pr_refs": pr_refs,
                "diffs": diffs,
                "ticket_text": ticket_text,
            }

            reviewers = _default_reviewers()
            tasks = []
            for rv in reviewers:
                ctx = ctx_qa if rv.name == "qa-evidence" else ctx_cr
                tasks.append(_run_reviewer_and_emit(rv, ctx, stream))
            outcomes = await asyncio.gather(*tasks)

            verdict = _synthesize(outcomes)
            try:
                for o in outcomes:
                    usage_ledger.record(
                        task=o["name"], ticket=ticket_key, pipeline_id=pipeline_id,
                        model=o.get("model"), usage=o.get("usage") or {},
                        is_error=(o.get("verdict") == "ERROR"),
                    )
                verdict["cost_usd"] = round(
                    sum((o.get("usage") or {}).get("cost_usd", 0) for o in outcomes), 6
                )
            except Exception:
                # Usage tracking is best-effort: it must never block the review gate.
                verdict.setdefault("cost_usd", 0.0)
            status = "pass" if verdict["verdict"] == "PASS" else "block"
            _pipeline_store.upsert(
                pipeline_id,
                {"councilStatus": status, "councilPayload": verdict},
            )
            append_audit({
                "event": "verdict",
                "ticket": ticket_key,
                "pipeline_id": pipeline_id,
                "verdict": verdict["verdict"],
                "reviewers": [{"name": r["name"], "verdict": r["verdict"]} for r in verdict["reviewers"]],
            })
            stream.append({"type": "verdict", **verdict})
        except Exception as e:
            err = {"verdict": "BLOCK", "rationale": f"council orchestrator crashed: {e}", "reviewers": []}
            _pipeline_store.upsert(pipeline_id, {"councilStatus": "block", "councilPayload": err})
            stream.append({"type": "verdict", **err})
            append_audit({"event": "verdict", "ticket": ticket_key, "pipeline_id": pipeline_id, "verdict": "BLOCK", "error": str(e)})
        finally:
            stream.end()

    asyncio.create_task(_runner())
    return council_stream_id


async def override(pipeline_id: str, reason: str, user: str) -> dict:
    """Mark a BLOCK as overridden. Raises ValueError if not in 'block' state."""
    state = _pipeline_store.get(pipeline_id)
    if not state or state.get("councilStatus") != "block":
        raise ValueError("override only valid when councilStatus == 'block'")
    if not reason or not reason.strip():
        raise ValueError("override reason required")
    override_payload = {
        "reason": reason.strip(),
        "user": user,
        "at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _pipeline_store.upsert(pipeline_id, {
        "councilStatus": "overridden",
        "councilOverride": override_payload,
    })
    append_audit({
        "event": "override",
        "pipeline_id": pipeline_id,
        "ticket": state.get("ticketKey", ""),
        "reason": override_payload["reason"],
        "user": user,
    })
    return override_payload


def append_audit(record: dict) -> None:
    """Append one JSONL line to the council audit log.

    The audit log is write-only as far as the running system is concerned;
    it exists for retrospective analysis of council decisions.

    Concurrency: `_AUDIT_LOCK` serializes writes within a single process.
    Multi-process deployments (e.g. `uvicorn --workers N`) are NOT protected —
    add an external lock or a separate logging service before running with
    more than one worker.

    Raises:
        TypeError: if `record` contains non-JSON-serializable values.
            Callers (hard-gate audit) must NOT swallow this — a lost audit
            line is worse than a crash.
        OSError: on I/O failure (disk full, read-only fs, etc.).
    """
    payload = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **record}
    line = json.dumps(payload, sort_keys=False)
    with _AUDIT_LOCK:
        os.makedirs(os.path.dirname(COUNCIL_AUDIT_PATH), exist_ok=True)
        with open(COUNCIL_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
