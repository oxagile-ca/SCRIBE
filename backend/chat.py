"""Wraps `claude -p --output-format stream-json` so the dashboard can
host a chat that's equivalent to running Claude Code in a terminal."""
import asyncio
import json
import os
import shlex
from typing import AsyncIterator, Optional

import usage_ledger
from config import CHAT_MODEL


CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CHAT_CWD = os.path.expanduser("~")


def _session_dir_for_current_user() -> str:
    """Derive the Claude Code per-project session dir from $HOME.

    Claude Code encodes the cwd by replacing `/` and `.` with `-`, so
    /Users/qa-engineer → -Users-qa-engineer. We just mirror that rule.
    Override with QA_DASH_SESSION_DIR if your install differs."""
    override = os.environ.get("QA_DASH_SESSION_DIR")
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser("~")
    slug = home.replace("/", "-").replace(".", "-")
    return os.path.expanduser(f"~/.claude/projects/{slug}")


SESSION_DIR = _session_dir_for_current_user()

# If `claude -p` produces no output for this many seconds, treat it as hung
# and kill the subprocess. Today's deploycli lesson: any unbounded subprocess can
# wedge silently and leave the user staring at a "…" forever.
CHAT_IDLE_TIMEOUT = 180  # 3 min between output lines

# Hard ceiling regardless of progress. Complex multi-tool sessions can run
# long, so this is generous; the idle timeout catches actual hangs faster.
CHAT_TOTAL_TIMEOUT = 600  # 10 min wall-clock per turn


def _session_exists(session_id: str) -> bool:
    """Check if a Claude Code session id is still on disk and resumable."""
    if not session_id:
        return False
    return os.path.exists(os.path.join(SESSION_DIR, f"{session_id}.jsonl"))


def _build_cmd(session_id: Optional[str]) -> list[str]:
    # argv list for create_subprocess_exec — the message is fed via STDIN, never as an
    # arg. The old shell + shlex.quote build ran through cmd.exe on Windows, which ignores
    # POSIX single quotes and mangled the message, so FRIDAY received a garbled prompt and
    # replied "looks like your message was cut off".
    parts = [
        CLAUDE_BIN,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]
    if CHAT_MODEL:
        parts += ["--model", CHAT_MODEL]
    if session_id:
        parts += ["--resume", session_id]
    return parts


def _extract_text(content):
    """Pull text out of a content block list (assistant or user message)."""
    out = []
    for block in content or []:
        if block.get("type") == "text":
            out.append(block.get("text", ""))
    return "".join(out)


async def chat_stream(message: str, session_id: Optional[str] = None) -> AsyncIterator[dict]:
    """Yield normalized events for the dashboard chat panel."""
    # Drop a stale session id so we start fresh instead of failing with
    # "No conversation found" — happens after manual cleanup or backend restarts.
    if session_id and not _session_exists(session_id):
        session_id = None
    cmd = _build_cmd(session_id)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=CHAT_CWD,
        limit=64 * 1024 * 1024,  # a large stream-json line would overrun the 64 KiB default
    )

    async def _feed_stdin() -> None:
        try:
            proc.stdin.write(message.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
    stdin_task = asyncio.create_task(_feed_stdin())

    sent_session = False
    start = asyncio.get_event_loop().time()
    killed_for_timeout = False
    chat_model: Optional[str] = None

    try:
        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > CHAT_TOTAL_TIMEOUT:
                yield {"type": "error", "msg": f"Chat exceeded {CHAT_TOTAL_TIMEOUT}s wall-clock — killing subprocess."}
                killed_for_timeout = True
                break
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=CHAT_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                yield {
                    "type": "error",
                    "msg": f"FRIDAY: no output for {CHAT_IDLE_TIMEOUT}s — subprocess looks hung. Killing.",
                }
                killed_for_timeout = True
                break
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            sid = event.get("session_id")
            if sid and not sent_session:
                yield {"type": "session", "session_id": sid}
                sent_session = True

            if etype == "assistant":
                msg = event.get("message", {}) or {}
                for block in msg.get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            yield {"type": "text", "data": text}
                    elif btype == "tool_use":
                        yield {
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": block.get("input", {}),
                        }

            elif etype == "user":
                # Tool results come back as user messages with tool_result blocks
                msg = event.get("message", {}) or {}
                for block in msg.get("content", []) or []:
                    if block.get("type") == "tool_result":
                        content = block.get("content", "")
                        if isinstance(content, list):
                            content = _extract_text(content)
                        yield {
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": str(content)[:4000],
                            "is_error": bool(block.get("is_error")),
                        }

            elif etype == "system":
                chat_model = usage_ledger.parse_model_from_init(event) or chat_model

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

        if killed_for_timeout and proc.returncode is None:
            # Don't sit on proc.wait() after a timeout — kill now and reap.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        if not killed_for_timeout and proc.returncode and proc.returncode != 0:
            err = await proc.stderr.read()
            yield {"type": "error", "msg": err.decode("utf-8", errors="replace")[:2000]}
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        stdin_task.cancel()
