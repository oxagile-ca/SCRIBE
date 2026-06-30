import asyncio
import json
import os
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


def test_run_and_finalize_happy_path(monkeypatch, tmp_path):
    # Stub every collaborator so we test orchestration only.
    # Create summary.json so the new guard passes.
    run_dir = tmp_path / "INV-9" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text('{"score": 94, "verdict": "PASS"}', encoding="utf-8")

    async def fake_qa_run(ticket_key, env_url, **kw):
        yield {"type": "log", "data": "x"}
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    monkeypatch.setattr(qa_orchestrator, "generate_html_report",
                        lambda k, r: (True, "ok", f"/evidence/{k}/runs/{r}/index.html"))
    monkeypatch.setattr(qa_orchestrator, "read_run_summary", lambda k, r: {"score": 94, "verdict": "PASS"})
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    async def fake_pdf(html, **kw): return str(tmp_path / "INV-9" / "runs" / "run-1" / "evidence.pdf")
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


def test_run_and_finalize_fails_when_no_summary(monkeypatch, tmp_path):
    """When summary.json is absent (browser blocked / Phase 2 skipped), yield done failure."""
    generate_called = []

    async def fake_qa_run(ticket_key, env_url, **kw):
        yield {"type": "log", "data": "x"}
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)

    def fake_generate(k, r):
        generate_called.append((k, r))
        return (True, "ok", f"/evidence/{k}/runs/{r}/index.html")
    monkeypatch.setattr(qa_orchestrator, "generate_html_report", fake_generate)
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(qa_orchestrator, "load_instance_config",
                        lambda: {"issueTracker": {"access": {"write": True}}})

    async def collect():
        out = []
        async for ev in qa_orchestrator.run_and_finalize("INV-9", "https://x", armed=True):
            out.append(ev)
        return out
    events = asyncio.run(collect())
    done = events[-1]
    assert done["type"] == "done"
    assert done["success"] is False
    err = done.get("error", "")
    assert "summary" in err.lower() or "evidence" in err.lower()
    assert generate_called == []


def test_run_and_finalize_qa_failure_stops_early(monkeypatch):
    async def fake_qa_run(ticket_key, env_url, **kw):
        yield {"type": "qa_complete", "success": False, "run_name": None, "error": "boom"}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    async def collect():
        return [ev async for ev in qa_orchestrator.run_and_finalize("INV-9", "https://x", armed=True)]
    events = asyncio.run(collect())
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is False


def test_read_run_summary_nested_confidence(tmp_path, monkeypatch):
    """read_run_summary extracts score from confidence.headline (newer format)."""
    # Newer format: top-level score + confidence dict (as seen in INV-643)
    summary_data = {
        "ticket": "INV-999",
        "score": 91,
        "verdict": "PASS — all good. Confidence 91/100.",
        "confidence": {"headline": 91, "band": "high", "explanation": "looks good"},
    }
    run_dir = tmp_path / "INV-999" / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps(summary_data), encoding="utf-8")
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))

    result = qa_orchestrator.read_run_summary("INV-999", "run-001")
    assert result["score"] == 91
    assert result["verdict"] == "PASS"


def test_read_run_summary_bare_confidence(tmp_path, monkeypatch):
    """read_run_summary extracts score when confidence is a bare int (older format, e.g. INV-620)."""
    summary_data = {
        "ticket": "INV-620",
        "verdict": "PASS-WITH-ISSUES",
        "confidence": 93,
    }
    run_dir = tmp_path / "INV-620" / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps(summary_data), encoding="utf-8")
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))

    result = qa_orchestrator.read_run_summary("INV-620", "run-001")
    assert result["score"] == 93
    assert result["verdict"] == "PASS-WITH-ISSUES"


def test_read_run_summary_missing_file(tmp_path, monkeypatch):
    """read_run_summary returns {score: None, verdict: None} when file is absent."""
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    result = qa_orchestrator.read_run_summary("INV-NOPE", "run-000")
    assert result == {"score": None, "verdict": None}


def test_finalize_overwrites_summary_with_canonical_score(monkeypatch, tmp_path):
    run_dir = tmp_path / "INV-700" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    # Agent wrote a WRONG score (90) dragged down by an advisory AXE fail; AC TCs all pass.
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-700", "verdict": "PASS-WITH-ISSUES",
        "score": {"pass": 2, "fail": 1, "total": 3, "pct": 67},
        "test_cases": [
            {"id": "TC-700-001", "status": "pass"},
            {"id": "TC-UV-1", "status": "pass"},
            {"id": "TC-UV-5", "status": "fail"},
            {"id": "TC-API-1", "status": "fail"},
        ],
    }), encoding="utf-8")

    async def fake_qa_run(*a, **k):
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(qa_orchestrator, "generate_html_report",
                        lambda k, r: (True, "ok", f"/evidence/{k}/runs/{r}/index.html"))
    async def fake_pdf(html, **kw): return None
    monkeypatch.setattr(qa_orchestrator.pdf_export, "export", fake_pdf)
    monkeypatch.setattr(qa_orchestrator, "load_instance_config", lambda: {})
    monkeypatch.setattr(qa_orchestrator, "compute_attach_gate", lambda *a, **k: False)

    async def drain():
        async for _ in qa_orchestrator.run_and_finalize("INV-700", "http://x", armed=False):
            pass
    import asyncio
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert out["score"]["pct"] == 100      # advisory fails excluded
    assert out["score"]["total"] == 2      # TC-700-001 + TC-UV-1
    assert out["verdict"] == "PASS"
    assert out["scoring"]["advisory_ids"] == ["TC-UV-5", "TC-API-1"]
