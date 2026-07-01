import asyncio
import json
import os
import qa_orchestrator


# --- Layer C: reconcile_ticket logic (injected resolvers; no live calls) ---

def test_reconcile_ticket_skips_without_vcs_or_prs():
    assert qa_orchestrator.reconcile_ticket("INV-1", {}) is None          # no vcs
    cfg = {"vcs": {"repos": ["xinventory-ux"]}}
    assert qa_orchestrator.reconcile_ticket(                              # vcs, but 0 PRs
        "INV-1", cfg, resolve_pr_refs=lambda k: []) is None


def test_reconcile_ticket_delegates_when_prs_found():
    cfg = {"vcs": {"repos": ["xinventory-ux"]}}
    refs = [{"owner": "O", "repo": "xinventory-ux", "id": 238}]
    seen = {}

    def run_reconcile(key, prs):
        seen["key"], seen["prs"] = key, prs
        return {"status": "ok", "divergences": [], "degraded_reason": None}

    res = qa_orchestrator.reconcile_ticket(
        "INV-651", cfg, resolve_pr_refs=lambda k: refs, run_reconcile=run_reconcile)
    assert res["status"] == "ok"
    assert seen == {"key": "INV-651", "prs": refs}


def test_reconcile_ticket_degrades_on_resolver_error():
    cfg = {"vcs": {"repos": ["xinventory-ux"]}}

    def boom(k):
        raise RuntimeError("linear 500")

    res = qa_orchestrator.reconcile_ticket("INV-1", cfg, resolve_pr_refs=boom)
    assert res["status"] == "degraded"
    assert "500" in res["degraded_reason"]


def _stub_finalize_collaborators(monkeypatch, tmp_path):
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(qa_orchestrator, "generate_html_report",
                        lambda k, r: (True, "ok", f"/evidence/{k}/runs/{r}/index.html"))

    async def fake_pdf(html, **kw):
        return None
    monkeypatch.setattr(qa_orchestrator.pdf_export, "export", fake_pdf)
    monkeypatch.setattr(qa_orchestrator, "load_instance_config", lambda: {})
    monkeypatch.setattr(qa_orchestrator, "compute_attach_gate", lambda *a, **k: False)

    async def fake_qa_run(*a, **k):
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)


def test_finalize_divergence_guard_blocks_clean_pass(monkeypatch, tmp_path):
    run_dir = tmp_path / "INV-800" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-800", "verdict": "PASS",
        "test_cases": [{"id": "TC-800-001", "status": "pass"}],
    }), encoding="utf-8")
    _stub_finalize_collaborators(monkeypatch, tmp_path)
    # A later main commit superseded a PR value -> one mapped divergence.
    monkeypatch.setattr(qa_orchestrator, "reconcile_ticket", lambda k, cfg: {
        "status": "ok", "degraded_reason": None,
        "divergences": [{"repo": "xinventory-ux", "path": "fees.py", "region": "MRDT",
                         "pr_hint": "MRDT = 0.03", "main_hint": "MRDT = 0.02"}]})

    async def drain():
        async for _ in qa_orchestrator.run_and_finalize("INV-800", "http://x", armed=False):
            pass
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "TC-RECON-1" in [t["id"] for t in out["test_cases"]]
    assert out["verdict"] == "PASS-WITH-ISSUES"          # needs-review -> not clean PASS
    assert out["reconcile"]["status"] == "ok"
    assert (run_dir / "reconcile.json").exists()


def test_finalize_degraded_reconcile_blocks_clean_pass(monkeypatch, tmp_path):
    run_dir = tmp_path / "INV-801" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-801", "verdict": "PASS",
        "test_cases": [{"id": "TC-801-001", "status": "pass"}],
    }), encoding="utf-8")
    _stub_finalize_collaborators(monkeypatch, tmp_path)
    monkeypatch.setattr(qa_orchestrator, "reconcile_ticket", lambda k, cfg: {
        "status": "degraded", "degraded_reason": "gh down", "divergences": []})

    async def drain():
        async for _ in qa_orchestrator.run_and_finalize("INV-801", "http://x", armed=False):
            pass
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "TC-RECON" in [t["id"] for t in out["test_cases"]]
    assert out["verdict"] != "PASS"                      # degraded can't silently pass


def test_finalize_skips_reconcile_when_none(monkeypatch, tmp_path):
    """reconcile_ticket -> None (no PRs) leaves scoring untouched and writes no reconcile.json."""
    run_dir = tmp_path / "INV-802" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-802", "verdict": "PASS",
        "test_cases": [{"id": "TC-802-001", "status": "pass"}],
    }), encoding="utf-8")
    _stub_finalize_collaborators(monkeypatch, tmp_path)
    monkeypatch.setattr(qa_orchestrator, "reconcile_ticket", lambda k, cfg: None)

    async def drain():
        async for _ in qa_orchestrator.run_and_finalize("INV-802", "http://x", armed=False):
            pass
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert out["verdict"] == "PASS"
    assert "reconcile" not in out
    assert not (run_dir / "reconcile.json").exists()


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


def test_finalize_scores_legacy_test_results_key(monkeypatch, tmp_path):
    """A summary using the legacy `test_results` key is scored, not stamped BLOCKED."""
    run_dir = tmp_path / "INV-702" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    # Old-format summary uses "test_results" instead of "test_cases"
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-702", "verdict": "UNKNOWN",
        "test_results": [
            {"id": "TC-702-001", "status": "pass"},
            {"id": "TC-702-002", "status": "pass"},
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
        async for _ in qa_orchestrator.run_and_finalize("INV-702", "http://x", armed=False):
            pass
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert out["verdict"] == "PASS"
    assert out["score"]["pct"] == 100
    assert out["score"]["total"] == 2


def test_finalize_handles_unreadable_summary(monkeypatch, tmp_path):
    """Malformed summary.json yields a done-failure event instead of crashing."""
    run_dir = tmp_path / "INV-703" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("{ not valid json", encoding="utf-8")

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

    async def collect():
        out = []
        async for ev in qa_orchestrator.run_and_finalize("INV-703", "http://x", armed=False):
            out.append(ev)
        return out
    events = asyncio.run(collect())

    done_events = [e for e in events if e.get("type") == "done"]
    assert done_events, "expected at least one done event"
    done = done_events[-1]
    assert done["success"] is False
    assert "unreadable" in (done.get("error") or "").lower()
