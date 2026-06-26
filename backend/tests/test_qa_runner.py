import asyncio
import qa_runner


def test_build_qa_command_exact_template():
    cmd = qa_runner.build_qa_command("INV-660", "https://app.example.com", "/qa-evidence-beeventory")
    assert cmd == "/qa-evidence-beeventory INV-660 run:qa-feature env:https://app.example.com --headless --auto-approve"


def test_build_runner_argv_mirrors_council(monkeypatch):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    argv = qa_runner.build_runner_argv("/qa-evidence-beeventory INV-1 ...", "claude-haiku-4-5")
    assert argv[:6] == ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode"]
    assert "bypassPermissions" in argv
    assert "--model" in argv and "claude-haiku-4-5" in argv
    assert argv[-1] == "/qa-evidence-beeventory INV-1 ..."


def test_build_runner_argv_no_model_omits_flag(monkeypatch):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    argv = qa_runner.build_runner_argv("prompt", None)
    assert "--model" not in argv
    assert argv[-1] == "prompt"


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
