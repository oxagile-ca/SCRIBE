"""Run the qa-evidence skill server-side via `claude -p`, streaming progress.

Generalizes council.py's subprocess pattern (argv vector, stream-json parse) for a
single long-running browser QA run. Closes the copy-paste gap (#9): instead of the
dashboard printing "paste this in Claude Code", the backend runs it.
"""
import asyncio
import json
import os

from agents import EVIDENCE_DIR  # ~/evidence
import config


def _claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN", "claude")


def build_qa_command(ticket_key: str, env_url: str, skill_cmd: str) -> str:
    """The exact template agents.run_test uses today (agents.py:587-588)."""
    return f"{skill_cmd} {ticket_key} run:qa-feature env:{env_url} --headless --auto-approve --isolated"


def build_runner_argv(command: str, model: str | None) -> list[str]:
    """Argv for create_subprocess_exec — mirrors council._build_reviewer_cmd."""
    argv = [
        _claude_bin(),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]
    if model:
        argv += ["--model", model]
    argv.append(command)
    return argv


def list_runs(ticket_key: str) -> set[str]:
    runs_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs")
    if os.path.isdir(runs_path):
        return set(os.listdir(runs_path))
    return set()


async def run(ticket_key, env_url, *, model=None, idle_timeout_s=300, total_timeout_s=1800):
    """Spawn the qa-evidence skill and stream events. Terminal event is qa_complete."""
    from instance_config import load_instance_config
    cfg = load_instance_config() or {}
    skill_cmd = cfg.get("skillCommand") or "/qa-evidence"
    command = build_qa_command(ticket_key, env_url, skill_cmd)
    model = model or getattr(config, "QA_EVIDENCE_MODEL", None)
    argv = build_runner_argv(command, model)

    baseline = list_runs(ticket_key)
    yield {"type": "log", "data": f"Running QA for {ticket_key} server-side…"}
    yield {"type": "progress", "pct": 5, "eta": "starting"}

    error = None
    killed = False
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        yield {"type": "qa_complete", "success": False, "run_name": None, "error": f"spawn failed: {e}"}
        return

    start = asyncio.get_running_loop().time()
    try:
        while True:
            if asyncio.get_running_loop().time() - start > total_timeout_s:
                error = f"QA run exceeded {total_timeout_s}s"
                killed = True
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout_s)
            except asyncio.TimeoutError:
                error = f"QA run idle for {idle_timeout_s}s"
                killed = True
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except Exception:
                continue
            if event.get("type") == "assistant":
                for block in (event.get("message", {}).get("content") or []):
                    if block.get("type") == "text" and block.get("text"):
                        yield {"type": "log", "data": block["text"][:300]}
            elif event.get("type") == "result":
                yield {"type": "progress", "pct": 90, "eta": "finalizing"}
    finally:
        if killed:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()

    if error:
        yield {"type": "qa_complete", "success": False, "run_name": None, "error": error}
        return
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
        yield {"type": "qa_complete", "success": False, "run_name": None,
               "error": f"claude exited {proc.returncode}: {stderr[-200:]}"}
        return

    new_runs = sorted(list_runs(ticket_key) - baseline)
    run_name = new_runs[-1] if new_runs else None
    if not run_name:
        yield {"type": "qa_complete", "success": False, "run_name": None,
               "error": "QA run produced no new evidence run"}
        return
    yield {"type": "log", "data": f"QA run complete: {run_name}"}
    yield {"type": "qa_complete", "success": True, "run_name": run_name, "error": None}
