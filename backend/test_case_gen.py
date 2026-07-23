"""Generate draft QA test cases for a ticket via the `claude` CLI.

The prompt building and reply parsing are pure (unit-tested); the CLI spawn is the
integration layer. Generated cases are stored as normal user-added cases so they
are editable/deletable and feed the QA run scope for free (via qa_targets).

Auth: reuses claude_env() — the user's claude.ai login, NOT the Anthropic API key —
so generation works on installs without a key, exactly like the QA runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import test_cases_store
from claude_env import claude_env

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
GEN_TIMEOUT = 120  # seconds; a single non-agentic draft should be quick


def build_generation_prompt(ticket_text: str) -> str:
    ticket_text = (ticket_text or "").strip() or "(no description provided)"
    return (
        "You are a senior QA engineer. Read the ticket below and draft 5-8 concise, "
        "independent, verifiable QA test cases covering its acceptance criteria, key "
        "flows, and important edge cases.\n\n"
        "Rules:\n"
        "- One clear action plus its expected result per case.\n"
        "- No numbering, no preamble, no commentary.\n"
        "- Return ONLY a JSON array of strings, nothing else.\n\n"
        f"Ticket:\n{ticket_text}\n"
    )


def _from_json(text: str) -> list[str] | None:
    """Extract a JSON array of strings from the model's reply, or None."""
    t = re.sub(r"```(?:json)?", "", text)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        arr = json.loads(t[start:end + 1])
    except Exception:
        return None
    if isinstance(arr, list):
        strs = [x for x in arr if isinstance(x, str)]
        return strs or None
    return None


def _from_lines(text: str) -> list[str]:
    """Fallback: pull bulleted / numbered / checkbox list items."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^[-*]\s*(?:\[[ xX]\]\s*)?(.+)$", line) or re.match(r"^\d+[.)]\s*(.+)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def parse_generated_cases(text: str, limit: int = 8) -> list[str]:
    """Turn the model's reply into a clean, deduped list of test-case strings."""
    if not text:
        return []
    cases = _from_json(text)
    if cases is None:
        cases = _from_lines(text)
    out: list[str] = []
    seen: set[str] = set()
    for c in cases:
        c = (c or "").strip()
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _extract_result_text(stdout: str) -> str:
    """`claude -p --output-format json` returns {..., "result": "<model text>"}."""
    stdout = (stdout or "").strip()
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict) and "result" in obj:
            return obj.get("result") or ""
    except Exception:
        pass
    return stdout


async def generate(key: str, ticket_text: str, *, path: str | None = None,
                   limit: int = 8) -> list[dict]:
    """Draft test cases for a ticket and store them as added cases. Returns the
    stored case dicts. Raises on spawn/timeout so the endpoint can report it."""
    prompt = build_generation_prompt(ticket_text)
    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN, "-p", "--output-format", "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.expanduser("~"),
        env=claude_env(),
    )
    try:
        out, _err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=GEN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("test-case generation timed out")
    cases = parse_generated_cases(_extract_result_text(out.decode(errors="replace")), limit=limit)
    stored = [test_cases_store.add_case(key, c, path=path) for c in cases]
    return [s for s in stored if s]
