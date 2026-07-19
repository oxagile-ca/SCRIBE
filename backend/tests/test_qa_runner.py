import asyncio
import qa_runner


def test_build_qa_command_exact_template():
    cmd = qa_runner.build_qa_command("INV-660", "https://app.example.com", "/qa-evidence-beeventory")
    assert cmd == "/qa-evidence-beeventory INV-660 run:qa-feature env:https://app.example.com --headless --auto-approve --isolated"


def test_build_runner_argv_mirrors_council(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    cfg = tmp_path / "mcp.json"; cfg.write_text("{}")
    monkeypatch.setenv("QA_MCP_CONFIG", str(cfg))
    argv = qa_runner.build_runner_argv("/qa-evidence-beeventory INV-1 ...", "claude-haiku-4-5")
    assert argv[0] == "claude" and argv[1] == "-p"
    assert "--output-format" in argv and "stream-json" in argv
    assert "--verbose" in argv
    assert "bypassPermissions" in argv
    assert "--model" in argv and "claude-haiku-4-5" in argv
    assert argv[-1] == "/qa-evidence-beeventory INV-1 ..."


def test_build_runner_argv_no_model_omits_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    cfg = tmp_path / "mcp.json"; cfg.write_text("{}")
    monkeypatch.setenv("QA_MCP_CONFIG", str(cfg))
    argv = qa_runner.build_runner_argv("prompt", None)
    assert "--model" not in argv
    assert argv[-1] == "prompt"


def test_build_runner_argv_injects_playwright_mcp(monkeypatch, tmp_path):
    """Headless `claude -p` does not load the Playwright plugin MCP (D3); the
    runner must wire it in via --mcp-config so Phase 2 can drive a browser."""
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    cfg = tmp_path / "mcp.json"; cfg.write_text("{}")
    monkeypatch.setenv("QA_MCP_CONFIG", str(cfg))
    argv = qa_runner.build_runner_argv("PROMPT", None)
    assert "--mcp-config" in argv
    i = argv.index("--mcp-config")
    assert argv[i + 1] == str(cfg)
    # --mcp-config is variadic: the token after its value MUST be a flag, never the
    # bare command, or claude treats the prompt as a second config file and dies.
    assert argv[i + 2].startswith("--")
    assert argv[-1] == "PROMPT"


def test_build_runner_argv_skips_absent_mcp_config(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("QA_MCP_CONFIG", str(tmp_path / "does-not-exist.json"))
    argv = qa_runner.build_runner_argv("PROMPT", None)
    assert "--mcp-config" not in argv
    assert argv[-1] == "PROMPT"


def test_resolve_model_never_haiku_by_default():
    m = qa_runner._resolve_model(None)
    assert "haiku" not in m.lower()


def test_resolve_model_overrides_explicit_haiku():
    # Even if a caller (or stale config) passes Haiku, QA execution must not use it.
    m = qa_runner._resolve_model("claude-haiku-4-5")
    assert "haiku" not in m.lower()


def test_resolve_model_keeps_explicit_strong_model():
    assert qa_runner._resolve_model("claude-opus-4-8") == "claude-opus-4-8"


class _FakeStdout:
    def __init__(self, lines): self._lines = list(lines)
    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStdout([])
        self.returncode = 0
    def kill(self): pass
    async def wait(self): return 0


def test_run_streams_and_completes(monkeypatch, tmp_path):
    # qa_runner detects the NEW run dir by diffing the runs/ folder before vs after.
    runs = tmp_path / "INV-9" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-06-25-old").mkdir()
    monkeypatch.setattr(qa_runner, "EVIDENCE_DIR", str(tmp_path))

    async def fake_exec(*args, **kwargs):
        # Simulate claude emitting one assistant line, then the skill creating a run dir
        # AND writing summary.json (a real, evidence-producing run).
        new = runs / "2026-06-25-new"
        new.mkdir()
        (new / "summary.json").write_text('{"score": 90}', encoding="utf-8")
        return _FakeProc([b'{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}\n'])
    monkeypatch.setattr(qa_runner.asyncio, "create_subprocess_exec", fake_exec)

    async def collect():
        events = []
        async for ev in qa_runner.run("INV-9", "https://x", model=None):
            events.append(ev)
        return events
    events = asyncio.run(collect())
    terminal = events[-1]
    assert terminal["type"] == "qa_complete"
    assert terminal["success"] is True
    assert terminal["run_name"] == "2026-06-25-new"
    import os
    assert os.path.exists(str(runs / "2026-06-25-new" / "agent-log.jsonl"))  # transcript saved


def test_run_marks_failure_when_no_summary(monkeypatch, tmp_path):
    """A run dir created without summary.json (Phase 2 aborted) must report FAILED —
    not masquerade as a completed run (the INV-683 case)."""
    import os
    runs = tmp_path / "INV-9" / "runs"
    runs.mkdir(parents=True)
    monkeypatch.setattr(qa_runner, "EVIDENCE_DIR", str(tmp_path))

    async def fake_exec(*args, **kwargs):
        (runs / "2026-06-25-empty").mkdir()          # scaffold, but no summary.json
        return _FakeProc([b'{"type":"assistant","message":{"content":[{"type":"text","text":"x"}]}}\n'])
    monkeypatch.setattr(qa_runner.asyncio, "create_subprocess_exec", fake_exec)

    async def collect():
        return [ev async for ev in qa_runner.run("INV-9", "https://x", model=None)]
    events = asyncio.run(collect())
    terminal = events[-1]
    assert terminal["type"] == "qa_complete"
    assert terminal["success"] is False
    assert "summary.json" in (terminal["error"] or "").lower()
    assert os.path.exists(str(runs / "2026-06-25-empty" / "agent-log.jsonl"))  # failed run still logged


# --- honest run outcome: a run dir without summary.json is a FAILURE, not success ---

def test_resolve_run_outcome_success_needs_summary(tmp_path):
    import os
    runs = tmp_path / "INV-1" / "runs" / "r-new"
    runs.mkdir(parents=True)
    (runs / "summary.json").write_text("{}", encoding="utf-8")
    name, ok, err = qa_runner._resolve_run_outcome(str(tmp_path), "INV-1", set())
    assert name == "r-new" and ok is True and err is None


def test_resolve_run_outcome_no_summary_is_failure(tmp_path):
    runs = tmp_path / "INV-1" / "runs" / "r-new"
    runs.mkdir(parents=True)  # dir created but Phase 2 never wrote summary.json
    name, ok, err = qa_runner._resolve_run_outcome(str(tmp_path), "INV-1", set())
    assert name == "r-new" and ok is False
    assert "summary.json" in (err or "").lower()


def test_resolve_run_outcome_no_new_run(tmp_path):
    (tmp_path / "INV-1" / "runs").mkdir(parents=True)
    name, ok, err = qa_runner._resolve_run_outcome(str(tmp_path), "INV-1", set())
    assert name is None and ok is False and err


def test_finalize_transcript_to_run_dir(tmp_path):
    import os
    partial = tmp_path / "p.jsonl"
    partial.write_text("l1\nl2\n", encoding="utf-8")
    (tmp_path / "INV-1" / "runs" / "r").mkdir(parents=True)
    dest = qa_runner._finalize_transcript(str(partial), str(tmp_path), "INV-1", "r")
    assert dest.endswith("agent-log.jsonl") and os.sep + "r" + os.sep in dest
    assert open(dest, encoding="utf-8").read() == "l1\nl2\n"
    assert not os.path.exists(str(partial))          # moved, not copied


def test_finalize_transcript_no_run_keeps_ticket_level(tmp_path):
    import os
    partial = tmp_path / "p.jsonl"
    partial.write_text("x\n", encoding="utf-8")
    (tmp_path / "INV-1").mkdir(parents=True)
    dest = qa_runner._finalize_transcript(str(partial), str(tmp_path), "INV-1", None)
    assert "agent-log-failed.jsonl" in dest        # a totally-failed run still leaves a log
    assert open(dest, encoding="utf-8").read() == "x\n"
