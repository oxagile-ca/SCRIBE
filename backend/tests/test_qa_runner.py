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
        # Simulate claude emitting one assistant line, then the skill creating a run dir.
        (runs / "2026-06-25-new").mkdir()
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
