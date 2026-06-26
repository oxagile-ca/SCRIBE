"""Shared QA pipeline: run -> report -> pdf -> (gated) Linear attach.

Used by both the single-ticket /api/qa-run endpoint and the auto-mode loop, so the
behaviour and the write-gate are identical in both paths.
"""
import json
import os

import qa_runner
import pdf_export
import linear_writer
from agents import generate_html_report, EVIDENCE_DIR
from instance_config import load_instance_config
from config import QA_EVIDENCE_MODEL


def compute_attach_gate(cfg: dict, *, armed: bool, manual: bool) -> bool:
    """Automatic attaches need the arm switch; a manual click needs only write."""
    write_flag = bool(((cfg or {}).get("issueTracker") or {}).get("access", {}).get("write", False))
    return write_flag and (manual or armed)


def resolve_env_url(cfg: dict, env_url: str) -> str:
    if env_url:
        return env_url
    statics = ((cfg or {}).get("environments") or {}).get("staticUrls") or []
    return statics[0] if statics else ""


def read_run_summary(ticket_key: str, run_name: str) -> dict:
    """Best-effort {score, verdict} from the run's summary.json."""
    path = os.path.join(EVIDENCE_DIR, ticket_key, "runs", run_name, "summary.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        return {"score": data.get("score"), "verdict": data.get("verdict")}
    except Exception:
        return {"score": None, "verdict": None}


async def run_and_finalize(ticket_key, env_url, *, armed, manual=False, model=None):
    cfg = load_instance_config() or {}
    env_url = resolve_env_url(cfg, env_url)
    model = model or QA_EVIDENCE_MODEL

    run_name = None
    async for ev in qa_runner.run(ticket_key, env_url, model=model):
        if ev.get("type") == "qa_complete":
            if not ev.get("success"):
                yield {"type": "done", "success": False, "report_url": "", "pdf": None,
                       "attached": False, "skipped_reason": None, "error": ev.get("error")}
                return
            run_name = ev["run_name"]
        else:
            yield ev

    ok, msg, report_url = generate_html_report(ticket_key, run_name)
    if not ok:
        yield {"type": "done", "success": False, "report_url": "", "pdf": None,
               "attached": False, "skipped_reason": None, "error": f"report failed: {msg}"}
        return
    yield {"type": "log", "data": f"Report generated: {report_url}"}

    html_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs", run_name, "index.html")
    pdf_path = await pdf_export.export(html_path)
    if pdf_path:
        yield {"type": "log", "data": "Evidence PDF created"}
    else:
        yield {"type": "log", "data": "PDF export unavailable — keeping HTML report"}

    attached, skipped_reason = False, None
    if compute_attach_gate(cfg, armed=armed, manual=manual):
        if not pdf_path:
            skipped_reason = "no PDF to attach"
        else:
            summary = read_run_summary(ticket_key, run_name)
            comment = linear_writer.build_comment_markdown(
                ticket_key, report_url, summary["score"], summary["verdict"])
            res = await linear_writer.attach_evidence(
                ticket_key, pdf_path, comment,
                token=os.environ.get("LINEAR_TOKEN", ""), write_allowed=True)
            attached = res["attached"]
            skipped_reason = res["skipped_reason"]
            if res["error"]:
                yield {"type": "log", "data": f"Linear attach error: {res['error']}"}
            elif attached:
                yield {"type": "log", "data": "Attached evidence to Linear"}
            else:
                yield {"type": "log", "data": f"Linear attach skipped: {skipped_reason}"}
    else:
        skipped_reason = "auto-publish not armed / write off"
        yield {"type": "log", "data": "Not published to Linear (gate closed)"}

    yield {"type": "done", "success": True, "report_url": report_url, "pdf": pdf_path,
           "attached": attached, "skipped_reason": skipped_reason, "error": None}
