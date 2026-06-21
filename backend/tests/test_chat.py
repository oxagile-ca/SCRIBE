"""Tests for chat resilience.

The invariant we're locking in: a `claude -p` subprocess that hangs cannot
silently stall the dashboard forever. Today's fix is two timeouts —
per-line idle (CHAT_IDLE_TIMEOUT) and total wall-clock (CHAT_TOTAL_TIMEOUT)
— both of which yield a visible error event and kill the subprocess.
"""
import asyncio
import json as _json
import os as _os

import pytest

import chat


@pytest.mark.asyncio
async def test_idle_timeout_emits_error_and_terminates(monkeypatch):
    """If the subprocess produces no output for CHAT_IDLE_TIMEOUT, we
    must yield an `error` event and stop, not hang forever."""
    monkeypatch.setattr(chat, "CHAT_IDLE_TIMEOUT", 0.2)
    monkeypatch.setattr(chat, "CHAT_TOTAL_TIMEOUT", 10)
    # Bypass shlex-quoted command building — inject a raw shell that
    # sleeps without producing any output.
    monkeypatch.setattr(
        chat,
        "_build_cmd",
        lambda message, sid: "sleep 30",
    )
    monkeypatch.setattr(chat, "_session_exists", lambda _sid: False)

    events = []
    async for evt in chat.chat_stream("hello"):
        events.append(evt)
        if len(events) > 5:
            break

    assert any(e["type"] == "error" and "hung" in e["msg"].lower() for e in events), events


@pytest.mark.asyncio
async def test_total_timeout_emits_error(monkeypatch):
    """If the subprocess keeps emitting output past CHAT_TOTAL_TIMEOUT,
    we must hard-stop with an error rather than streaming indefinitely."""
    monkeypatch.setattr(chat, "CHAT_IDLE_TIMEOUT", 5)
    monkeypatch.setattr(chat, "CHAT_TOTAL_TIMEOUT", 0.3)
    # Spin output forever via a tight shell loop. CLAUDE_BIN is passed
    # through `shlex.quote` so we route around that by overriding _build_cmd.
    monkeypatch.setattr(
        chat,
        "_build_cmd",
        lambda message, sid: "while true; do echo '{}'; sleep 0.05; done",
    )
    monkeypatch.setattr(chat, "_session_exists", lambda _sid: False)

    events = []
    async for evt in chat.chat_stream("hello"):
        events.append(evt)
        if len(events) > 30:
            break

    assert any(e["type"] == "error" and "wall-clock" in e["msg"] for e in events), events


@pytest.mark.asyncio
async def test_clean_exit_no_error_emitted(monkeypatch):
    """A subprocess that exits cleanly (exit 0) without producing valid
    stream-json should not emit a spurious error event — the loop just ends."""
    monkeypatch.setattr(chat, "CHAT_IDLE_TIMEOUT", 5)
    monkeypatch.setattr(chat, "CHAT_TOTAL_TIMEOUT", 5)
    monkeypatch.setattr(
        chat,
        "_build_cmd",
        lambda message, sid: "echo '' ; true",
    )
    monkeypatch.setattr(chat, "_session_exists", lambda _sid: False)

    events = []
    async for evt in chat.chat_stream("hello"):
        events.append(evt)

    assert all(e.get("type") != "error" for e in events), events


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
