import asyncio
import qa_orchestrator


def test_gate_truth_table():
    write_on = {"issueTracker": {"access": {"write": True}}}
    write_off = {"issueTracker": {"access": {"write": False}}}
    assert qa_orchestrator.compute_attach_gate(write_on, armed=True, manual=False) is True
    assert qa_orchestrator.compute_attach_gate(write_on, armed=False, manual=False) is False
    assert qa_orchestrator.compute_attach_gate(write_on, armed=False, manual=True) is True
    assert qa_orchestrator.compute_attach_gate(write_off, armed=True, manual=True) is False
    assert qa_orchestrator.compute_attach_gate({}, armed=True, manual=True) is False


def test_resolve_env_url_prefers_arg_then_static():
    cfg = {"environments": {"staticUrls": ["https://static.example"]}}
    assert qa_orchestrator.resolve_env_url(cfg, "https://given") == "https://given"
    assert qa_orchestrator.resolve_env_url(cfg, "") == "https://static.example"


def test_run_and_finalize_happy_path(monkeypatch):
    # Stub every collaborator so we test orchestration only.
    async def fake_qa_run(ticket_key, env_url, **kw):
        yield {"type": "log", "data": "x"}
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    monkeypatch.setattr(qa_orchestrator, "generate_html_report",
                        lambda k, r: (True, "ok", f"/evidence/{k}/runs/{r}/index.html"))
    monkeypatch.setattr(qa_orchestrator, "read_run_summary", lambda k, r: {"score": 94, "verdict": "PASS"})
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", "/ev")
    async def fake_pdf(html, **kw): return "/ev/INV-9/runs/run-1/evidence.pdf"
    monkeypatch.setattr(qa_orchestrator.pdf_export, "export", fake_pdf)
    monkeypatch.setattr(qa_orchestrator, "load_instance_config",
                        lambda: {"issueTracker": {"access": {"write": True}}})
    async def fake_attach(*a, **k): return {"attached": True, "skipped_reason": None, "error": None}
    monkeypatch.setattr(qa_orchestrator.linear_writer, "attach_evidence", fake_attach)
    monkeypatch.setenv("LINEAR_TOKEN", "tok")

    async def collect():
        out = []
        async for ev in qa_orchestrator.run_and_finalize("INV-9", "https://x", armed=True):
            out.append(ev)
        return out
    events = asyncio.run(collect())
    done = events[-1]
    assert done["type"] == "done"
    assert done["success"] is True
    assert done["attached"] is True
    assert done["report_url"].endswith("index.html")


def test_run_and_finalize_qa_failure_stops_early(monkeypatch):
    async def fake_qa_run(ticket_key, env_url, **kw):
        yield {"type": "qa_complete", "success": False, "run_name": None, "error": "boom"}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    async def collect():
        return [ev async for ev in qa_orchestrator.run_and_finalize("INV-9", "https://x", armed=True)]
    events = asyncio.run(collect())
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is False
