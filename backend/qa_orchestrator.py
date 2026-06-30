"""Shared QA pipeline: run -> report -> pdf -> (gated) Linear attach.

Used by both the single-ticket /api/qa-run endpoint and the auto-mode loop, so the
behaviour and the write-gate are identical in both paths.
"""
import json
import os

import qa_runner
import pdf_export
import linear_writer
import qa_scoring
from agents import generate_html_report, EVIDENCE_DIR
from instance_config import load_instance_config
from config import QA_RUNNER_MODEL


def compute_attach_gate(cfg: dict, *, armed: bool, manual: bool) -> bool:
    """Automatic attaches need the arm switch; a manual click needs only write."""
    write_flag = bool((((cfg or {}).get("issueTracker") or {}).get("access") or {}).get("write", False))
    return write_flag and (manual or armed)


def resolve_env_url(cfg: dict, env_url: str) -> str:
    if env_url:
        return env_url
    statics = ((cfg or {}).get("environments") or {}).get("staticUrls") or []
    return statics[0] if statics else ""


def read_run_summary(ticket_key: str, run_name: str) -> dict:
    """Best-effort {score, verdict} from the run's summary.json.

    Handles multiple summary shapes found in the wild:
    - Newer format: top-level ``score`` int + ``confidence`` dict with ``headline``.
    - Older format: ``confidence`` as a bare int (no top-level ``score``).
    - Fallback: ``score_breakdown.headline``.
    Never raises — returns {score: None, verdict: None} on any error.
    """
    path = os.path.join(EVIDENCE_DIR, ticket_key, "runs", run_name, "summary.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}

        # --- score extraction ---
        score = data.get("score")
        # If score is a tally dict {pass, fail, total, pct, ...} normalise to int
        if isinstance(score, dict):
            pct = score.get("pct")
            if pct is not None:
                score = round(pct)
            else:
                total = score.get("total") or 0
                passed = score.get("pass") or 0
                score = round(100 * passed / total) if total else None
        # Fall back through confidence shapes
        if score is None:
            raw_conf = data.get("confidence")
            if isinstance(raw_conf, dict):
                score = raw_conf.get("headline")
            elif isinstance(raw_conf, (int, float)):
                score = raw_conf
        if score is None:
            score_bd = data.get("score_breakdown")
            if isinstance(score_bd, dict):
                score = score_bd.get("headline")

        # --- verdict extraction ---
        verdict = data.get("verdict") or data.get("verdict_reason") or None
        # Normalise to the first word (e.g. "PASS — explanation…" -> "PASS")
        if verdict:
            verdict = verdict.split()[0] if verdict else None

        return {"score": score, "verdict": verdict}
    except Exception:
        return {"score": None, "verdict": None}


async def run_and_finalize(ticket_key, env_url, *, armed, manual=False, model=None):
    cfg = load_instance_config() or {}
    env_url = resolve_env_url(cfg, env_url)
    model = model or QA_RUNNER_MODEL  # QA execution never uses Haiku (qa_runner guards too)

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

    summary_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs", run_name, "summary.json")
    if not os.path.exists(summary_path):
        yield {"type": "done", "success": False, "report_url": "", "pdf": None,
               "attached": False, "skipped_reason": None,
               "error": "QA run captured no evidence (summary.json missing) — likely browser blocked or did not execute Phase 2"}
        return

    # Canonical score: deterministic, backend-authoritative. Overwrites the agent's
    # self-reported number so advisory scans (API smoke, AXE, etc.) can't move the
    # headline. See docs/superpowers/specs/2026-06-29-qa-scoring-policy-design.md.
    try:
        with open(summary_path, encoding="utf-8") as _f:
            _summary = json.load(_f)
    except (json.JSONDecodeError, OSError) as _e:
        yield {"type": "done", "success": False, "report_url": "", "pdf": None,
               "attached": False, "skipped_reason": None,
               "error": f"summary.json unreadable: {_e}"}
        return
    # Match the consumer's key fallback (agents.generate_html_report supports the
    # legacy `test_results` key alongside the current `test_cases`); scoring only an
    # empty `test_cases` would wrongly stamp an old-format run BLOCKED.
    _tcs = _summary.get("test_cases") or _summary.get("test_results") or []
    _canon = qa_scoring.compute_score(_tcs)
    _summary["score"] = {"pass": _canon["pass"], "fail": _canon["fail"],
                         "blocked": _canon["blocked"], "total": _canon["total"],
                         "pct": _canon["pct"]}
    _summary["verdict"] = _canon["verdict"]
    _summary["scoring"] = {"scoring_ids": _canon["scoring_ids"],
                           "advisory_ids": _canon["advisory_ids"]}
    with open(summary_path, "w", encoding="utf-8") as _f:
        json.dump(_summary, _f, indent=2)

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
