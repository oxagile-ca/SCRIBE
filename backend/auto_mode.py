"""Auto mode: background loop that QAs eligible tickets and (gated) publishes.

State is persisted via PipelineStore.set_meta so the toggle survives restarts.
Concurrency respects SCRIBE_AUTOMODE_CONCURRENCY (default 1) for a controllable demo.
"""
import asyncio
import os
import uuid

import qa_orchestrator
import linear_writer
from agents import generate_html_report, EVIDENCE_DIR
from instance_config import load_instance_config

_STATE_KEY = "automode"
_PROCESSED_KEY = "automode_processed"
_store = None
_streams = None
_active: set[str] = set()

_PRIORITY_ORDER = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4, "": 5}
POLL_SEC = int(os.environ.get("SCRIBE_AUTOMODE_POLL_SEC", "60"))
CONCURRENCY = int(os.environ.get("SCRIBE_AUTOMODE_CONCURRENCY", "1"))


def configure(store, streams_registry) -> None:
    global _store, _streams
    _store = store
    _streams = streams_registry


def get_state() -> dict:
    raw = _store.get_meta(_STATE_KEY, None) if _store else None
    if not isinstance(raw, dict):
        return {"enabled": False, "armed": False}
    return {"enabled": bool(raw.get("enabled")), "armed": bool(raw.get("armed"))}


def set_state(*, enabled=None, armed=None) -> None:
    was_enabled = get_state()["enabled"]
    cur = get_state()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if armed is not None:
        cur["armed"] = bool(armed)
    if _store:
        _store.set_meta(_STATE_KEY, cur)
    if enabled and not was_enabled:
        reset_processed()


def get_processed() -> set:
    raw = _store.get_meta(_PROCESSED_KEY, None) if _store else None
    return set(raw) if isinstance(raw, list) else set()


def mark_processed(ticket_key: str) -> None:
    if not _store:
        return
    cur = get_processed()
    cur.add(ticket_key)
    _store.set_meta(_PROCESSED_KEY, sorted(cur))


def reset_processed() -> None:
    if _store:
        _store.set_meta(_PROCESSED_KEY, [])


def eligible_tickets(tickets: list[dict], skip=frozenset()) -> list[dict]:
    ready = [t for t in tickets
             if t.get("statusCategory") == "ready_for_qa" and t.get("key") not in skip]
    ready.sort(key=lambda t: _PRIORITY_ORDER.get(t.get("priority", ""), 5))
    return ready


def _latest_run(ticket_key: str) -> str | None:
    runs_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs")
    if not os.path.isdir(runs_path):
        return None
    runs = sorted(os.listdir(runs_path))
    return runs[-1] if runs else None


async def attach_latest(ticket_key: str):
    """Manual attach of the latest existing run (write-flag only, manual=True)."""
    cfg = load_instance_config() or {}
    if not qa_orchestrator.compute_attach_gate(cfg, armed=False, manual=True):
        yield {"type": "done", "success": False, "attached": False,
               "skipped_reason": "write permission off", "error": None}
        return
    run_name = _latest_run(ticket_key)
    if not run_name:
        yield {"type": "done", "success": False, "attached": False,
               "skipped_reason": "no evidence run found", "error": None}
        return
    ok, msg, report_url = generate_html_report(ticket_key, run_name)
    if not ok:
        yield {"type": "done", "success": False, "attached": False,
               "skipped_reason": f"report failed: {msg}", "error": None}
        return
    html_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs", run_name, "index.html")
    import pdf_export
    pdf_path = await pdf_export.export(html_path)
    if not pdf_path:
        yield {"type": "done", "success": False, "attached": False,
               "skipped_reason": "PDF export unavailable", "error": None}
        return
    summary = qa_orchestrator.read_run_summary(ticket_key, run_name)
    comment = linear_writer.build_comment_markdown(ticket_key, report_url, summary["score"], summary["verdict"])
    res = await linear_writer.attach_evidence(
        ticket_key, pdf_path, comment, token=os.environ.get("LINEAR_TOKEN", ""), write_allowed=True)
    yield {"type": "done", "success": res["attached"], "attached": res["attached"],
           "skipped_reason": res["skipped_reason"], "error": res["error"], "report_url": report_url}


async def _process(ticket_key: str, env_url: str) -> None:
    _active.add(ticket_key)
    state = get_state()
    stream_id = str(uuid.uuid4())
    stream = _streams.create(stream_id) if _streams else None
    try:
        async for ev in qa_orchestrator.run_and_finalize(
            ticket_key, env_url, armed=state["armed"], manual=False):
            if stream:
                stream.append(ev)
    except Exception as e:
        if stream:
            stream.append({"type": "error", "msg": str(e)})
    finally:
        if stream:
            stream.end()
        _active.discard(ticket_key)
        mark_processed(ticket_key)


async def run_loop() -> None:
    import linear_client
    while True:
        try:
            if get_state()["enabled"] and len(_active) < CONCURRENCY:
                cfg = load_instance_config() or {}
                issue = cfg.get("issueTracker") or {}
                if issue.get("type") == "linear":
                    tickets = await linear_client.get_tickets(
                        os.environ.get("LINEAR_TOKEN", ""), issue.get("projects") or [])
                else:
                    tickets = []
                # categorize so eligible_tickets can filter
                from status_map import categorize_status, resolve_status_mapping
                mapping = resolve_status_mapping(cfg, issue.get("type") or "jira")
                for t in tickets:
                    t["statusCategory"] = categorize_status(t.get("status", ""), mapping)
                skip = get_processed() | _active
                for t in eligible_tickets(tickets, skip=skip):
                    if len(_active) >= CONCURRENCY:
                        break
                    if t["key"] in _active:
                        continue
                    env_url = qa_orchestrator.resolve_env_url(cfg, "")
                    asyncio.create_task(_process(t["key"], env_url))
                    break  # one new ticket per poll; keeps the demo controllable
        except Exception:
            pass
        await asyncio.sleep(POLL_SEC)
