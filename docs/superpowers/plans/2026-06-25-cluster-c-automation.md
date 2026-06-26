# Cluster C — QA Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the copy-paste QA-evidence flow with a one-click server-side run (#9), add a background auto-mode loop that produces a PDF evidence report per ticket and optionally attaches it to Linear (#10), and hide the header cost figures (#4).

**Architecture:** Reuse the two patterns the backend already proves: `council.py`'s `claude -p` subprocess + stream-json parsing (for `qa_runner`), and `auto_provision.py`'s forever-loop registered in FastAPI startup (for `auto_mode`). New backend modules are small and single-purpose; the frontend reuses the existing `subscribeSSE` lane plumbing. PDF is headless Chrome (no new deps). Linear writes are gated by a double check: the `issueTracker.access.write` flag AND a default-off "auto-publish" arm switch; a separate manual "Attach to Linear" button needs only the write flag.

**Tech Stack:** Python 3 (FastAPI/uvicorn, asyncio, httpx), pytest + pytest-asyncio; React 18 + TypeScript + Vite (plain CSS); headless Chrome for HTML→PDF.

## Global Constraints

- **Python interpreter:** `C:\Users\ankit\SCRIBE\.venv\Scripts\python.exe` (the Windows `python3` is a Store shim with no deps — never use it).
- **Run backend tests from** `C:\Users\ankit\SCRIBE\backend` with `..\.venv\Scripts\python.exe -m pytest`.
- **No new pip dependencies.** Use `httpx` (already pinned) for HTTP and `subprocess`/headless Chrome for PDF. Do NOT add playwright/weasyprint/pdfkit.
- **Secrets:** read tokens from `os.environ` (e.g. `os.environ.get("LINEAR_TOKEN", "")`). There is no `${secret:}` runtime resolver; `.secrets.env` is loaded into `os.environ` at server start.
- **QA model:** `from config import QA_EVIDENCE_MODEL` (currently `claude-haiku-4-5`).
- **Skill command:** `(load_instance_config() or {}).get("skillCommand")` (currently `/qa-evidence-beeventory`); default `/qa-evidence`.
- **Tracker:** Linear; `issueTracker.access` currently `{read:true, write:true}`. Read the write flag defensively: `(cfg.get("issueTracker") or {}).get("access", {}).get("write", False)`.
- **Stream pattern (verbatim):** `stream_id = str(uuid.uuid4())` → `streams.create(stream_id)` → `asyncio.create_task(_run_stream(stream_id, <async-gen>))` → return `{"streamId": stream_id}`.
- **Claude subprocess (verbatim argv, never shell):** `[claude_bin, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions", (--model M), prompt]`. `claude_bin = os.environ.get("CLAUDE_BIN", "claude")`.
- **Evidence layout:** `EVIDENCE_DIR = ~/evidence`; runs at `{EVIDENCE_DIR}/{key}/runs/{run}/index.html`. `agents.generate_html_report(ticket_key, run_name=None) -> (success: bool, message: str, report_url: str)`.
- **Frontend has no test runner.** "Verify" for frontend tasks = `npm run build` (tsc typecheck) passes + manual check in the running app at `http://localhost:5173`.
- **Commit after every task.** Branch is `feat/cluster-c-automation`.

---

## File structure

**New backend modules** (each one responsibility):
- `backend/pdf_export.py` — HTML→PDF via headless Chrome.
- `backend/qa_runner.py` — spawn the qa-evidence skill via `claude -p`, stream events (#9 core).
- `backend/linear_writer.py` — Linear write client: resolve issue id, upload PDF, post comment.
- `backend/qa_orchestrator.py` — the shared "run → report → pdf → (gated) attach" generator + the attach gate.
- `backend/auto_mode.py` — enable/arm state + background loop (#10).

**Modified backend:**
- `backend/server.py` — new endpoints + register the auto-mode loop.

**Modified frontend:**
- `frontend/src/components/TopBar.tsx` — remove cost display (#4); add Auto Mode controls (#10).
- `frontend/src/components/LaneCard.tsx` — add "Run QA" + "Attach to Linear" buttons.
- `frontend/src/App.tsx` — wire Run QA / Attach handlers.
- `frontend/src/api.ts` — new API functions.

**New tests:** `backend/tests/test_pdf_export.py`, `test_qa_runner.py`, `test_linear_writer.py`, `test_qa_orchestrator.py`, `test_auto_mode.py`.

---

## Task 1: Hide header cost figures (#4)

**Files:**
- Modify: `frontend/src/components/TopBar.tsx` (remove spend state/effect at `:55-62`, span at `:95-100`, and the `UsageSummary`/`getUsageSummary` imports at `:2-3`).

**Interfaces:** none consumed/produced (pure removal).

- [ ] **Step 1: Remove the spend import**

In `frontend/src/components/TopBar.tsx`, change the two import lines:
```tsx
import { Ticket, UsageSummary } from '../types'
import { fetchVersion, getUsageSummary } from '../api'
```
to:
```tsx
import { Ticket } from '../types'
import { fetchVersion } from '../api'
```

- [ ] **Step 2: Remove the spend state + polling effect**

Delete this block (currently `TopBar.tsx:55-62`):
```tsx
  const [spend, setSpend] = useState<UsageSummary | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => getUsageSummary().then(s => { if (alive) setSpend(s) }).catch(() => {})
    load()
    const handle = setInterval(load, 30000)
    return () => { alive = false; clearInterval(handle) }
  }, [])
```

- [ ] **Step 3: Remove the spend display span**

Delete this block (currently `TopBar.tsx:95-100`):
```tsx
        {spend && (
          <span className="top-bar__spend" title="AI spend — today / all-time"
                style={{ fontSize: 12, color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>
            ${spend.today.cost_usd.toFixed(2)} today · ${spend.allTime.cost_usd.toFixed(2)} all-time
          </span>
        )}
```

- [ ] **Step 4: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds with no TS errors about `spend`/`getUsageSummary`. Load `http://localhost:5173` — the header no longer shows the `$X today · $Y all-time` text; version/refresh/etc unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TopBar.tsx
git commit -m "feat(#4): hide header token-cost figures"
```

---

## Task 2: `pdf_export.py` — headless-Chrome HTML→PDF

**Files:**
- Create: `backend/pdf_export.py`
- Test: `backend/tests/test_pdf_export.py`

**Interfaces:**
- Produces:
  - `find_browser() -> str | None` — path to chrome/edge or None.
  - `build_chrome_args(browser: str, html_path: str, pdf_path: str) -> list[str]`
  - `async export(html_path: str, pdf_path: str | None = None, timeout_s: int = 60) -> str | None` — returns the pdf path on success, `None` on any failure (never raises).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pdf_export.py`:
```python
import os
import pdf_export


def test_build_chrome_args_has_headless_and_print_to_pdf():
    args = pdf_export.build_chrome_args(
        r"C:\chrome.exe", r"C:\ev\index.html", r"C:\ev\out.pdf"
    )
    assert args[0] == r"C:\chrome.exe"
    assert "--headless=new" in args
    assert "--print-to-pdf=C:\\ev\\out.pdf" in args
    # the source html is passed as a file:// URL, last arg
    assert args[-1].startswith("file:///")
    assert args[-1].endswith("index.html")


def test_find_browser_returns_path_or_none(monkeypatch):
    # When a known path exists, it is returned.
    monkeypatch.setattr(pdf_export.os.path, "exists", lambda p: p == pdf_export.CHROME_CANDIDATES[0])
    assert pdf_export.find_browser() == pdf_export.CHROME_CANDIDATES[0]
    # When none exist and no env override, returns None.
    monkeypatch.setattr(pdf_export.os.path, "exists", lambda p: False)
    monkeypatch.delenv("SCRIBE_CHROME_PATH", raising=False)
    assert pdf_export.find_browser() is None


def test_export_returns_none_when_no_browser(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html><body>hi</body></html>", encoding="utf-8")
    monkeypatch.setattr(pdf_export, "find_browser", lambda: None)
    import asyncio
    result = asyncio.run(pdf_export.export(str(html)))
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_pdf_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_export'`.

- [ ] **Step 3: Write the implementation**

Create `backend/pdf_export.py`:
```python
"""HTML -> PDF via headless Chrome. No external Python dependency.

The evidence report (index.html) is self-contained (base64-embedded images), so a
plain headless Chrome print-to-pdf renders it offline and faithfully.
"""
import asyncio
import os
from pathlib import Path

# Standard install paths on this Windows machine; Edge is a Chromium fallback.
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    """Return a usable Chromium binary path, or None. SCRIBE_CHROME_PATH overrides."""
    override = os.environ.get("SCRIBE_CHROME_PATH")
    if override and os.path.exists(override):
        return override
    for candidate in CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def build_chrome_args(browser: str, html_path: str, pdf_path: str) -> list[str]:
    """Argv for a headless print-to-pdf. Last arg is the source as a file:// URL."""
    src_url = Path(html_path).resolve().as_uri()
    return [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        src_url,
    ]


async def export(html_path: str, pdf_path: str | None = None, timeout_s: int = 60) -> str | None:
    """Convert html_path -> pdf_path (default: sibling evidence.pdf). Returns the pdf
    path on success, None on any failure. Never raises — the caller degrades to HTML."""
    if not os.path.exists(html_path):
        return None
    browser = find_browser()
    if not browser:
        return None
    if pdf_path is None:
        pdf_path = os.path.join(os.path.dirname(html_path), "evidence.pdf")
    args = build_chrome_args(browser, html_path, pdf_path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
    except Exception:
        return None
    if proc.returncode == 0 and os.path.exists(pdf_path):
        return pdf_path
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_pdf_export.py -v`
Expected: 3 passed.

- [ ] **Step 5: (Optional) real smoke**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -c "import asyncio,pdf_export,tempfile,os; p=os.path.join(tempfile.gettempdir(),'t.html'); open(p,'w').write('<h1>PDF OK</h1>'); print(asyncio.run(pdf_export.export(p)))"`
Expected: prints a path ending in `evidence.pdf`, and that file exists.

- [ ] **Step 6: Commit**

```bash
git add backend/pdf_export.py backend/tests/test_pdf_export.py
git commit -m "feat(pdf): headless-Chrome HTML->PDF export (no new deps)"
```

---

## Task 3: `qa_runner.py` — server-side qa-evidence run (#9 core)

**Files:**
- Create: `backend/qa_runner.py`
- Test: `backend/tests/test_qa_runner.py`

**Interfaces:**
- Consumes: `config.QA_EVIDENCE_MODEL`, `instance_config.load_instance_config`, `agents.EVIDENCE_DIR`.
- Produces:
  - `build_qa_command(ticket_key: str, env_url: str, skill_cmd: str) -> str`
  - `build_runner_argv(command: str, model: str | None) -> list[str]`
  - `list_runs(ticket_key: str) -> set[str]`
  - `async run(ticket_key: str, env_url: str, *, model: str | None = None, idle_timeout_s: int = 300, total_timeout_s: int = 1800) -> AsyncIterator[dict]` — yields `{"type":"log"|"progress"}` events during the run and exactly one terminal `{"type":"qa_complete", "success": bool, "run_name": str|None, "error": str|None}`. Does NOT yield a `"done"` event (the orchestrator owns that).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_qa_runner.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_qa_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qa_runner'`.

- [ ] **Step 3: Write the implementation**

Create `backend/qa_runner.py`:
```python
"""Run the qa-evidence skill server-side via `claude -p`, streaming progress.

Generalizes council.py's subprocess pattern (argv vector, stream-json parse) for a
single long-running browser QA run. Closes the copy-paste gap (#9): instead of the
dashboard printing "paste this in Claude Code", the backend runs it.
"""
import asyncio
import json
import os

from agents import EVIDENCE_DIR  # ~/evidence


def _claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN", "claude")


def build_qa_command(ticket_key: str, env_url: str, skill_cmd: str) -> str:
    """The exact template agents.run_test uses today (agents.py:587-588)."""
    return f"{skill_cmd} {ticket_key} run:qa-feature env:{env_url} --headless --auto-approve"


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

    start = asyncio.get_event_loop().time()
    try:
        while True:
            if asyncio.get_event_loop().time() - start > total_timeout_s:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_qa_runner.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/qa_runner.py backend/tests/test_qa_runner.py
git commit -m "feat(#9): qa_runner — server-side qa-evidence via claude -p"
```

---

## Task 4: `linear_writer.py` — Linear write client (PDF attach + comment)

**Files:**
- Create: `backend/linear_writer.py`
- Test: `backend/tests/test_linear_writer.py`

**Interfaces:**
- Consumes: `os.environ["LINEAR_TOKEN"]`, `httpx`.
- Produces:
  - `build_comment_markdown(ticket_key: str, report_url: str, score, verdict: str | None) -> str`
  - `async resolve_issue_id(key: str, token: str) -> str | None`
  - `async attach_evidence(ticket_key: str, pdf_path: str, comment_md: str, *, token: str, write_allowed: bool) -> dict` — returns `{"attached": bool, "skipped_reason": str|None, "error": str|None}`. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_linear_writer.py`:
```python
import asyncio
import linear_writer


def test_build_comment_markdown_includes_score_and_verdict():
    md = linear_writer.build_comment_markdown("INV-660", "/evidence/INV-660/runs/r/index.html", 94, "PASS")
    assert "INV-660" in md
    assert "94" in md
    assert "PASS" in md


def test_attach_skips_when_write_not_allowed():
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", "x.pdf", "c", token="tok", write_allowed=False))
    assert res["attached"] is False
    assert res["skipped_reason"] and "write" in res["skipped_reason"].lower()


def test_attach_skips_when_no_token():
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", "x.pdf", "c", token="", write_allowed=True))
    assert res["attached"] is False
    assert res["skipped_reason"] and "token" in res["skipped_reason"].lower()


def test_attach_skips_when_pdf_missing(tmp_path):
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", str(tmp_path / "nope.pdf"), "c", token="tok", write_allowed=True))
    assert res["attached"] is False
    assert res["skipped_reason"] and "pdf" in res["skipped_reason"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_linear_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'linear_writer'`.

- [ ] **Step 3: Write the implementation**

Create `backend/linear_writer.py`:
```python
"""Linear WRITE client: resolve an issue id, upload a PDF asset, post a comment.

Mirrors linear_client.py's auth/endpoint/POST style (raw API key in Authorization,
JSON {query, variables} to the GraphQL endpoint). Read tokens from os.environ.
Linear file attach is a two-step flow: `fileUpload` returns a signed S3 URL +
headers; PUT the bytes there; then reference the assetUrl in `commentCreate`.

SPIKE NOTE: validate the fileUpload->PUT->commentCreate sequence and the issue-id
resolver against the live API once before relying on it (see plan "Open risks").
"""
import os

import httpx

LINEAR_API = "https://api.linear.app/graphql"

_ISSUE_ID_QUERY = """
query($number: Float!, $teamKey: String!) {
  issues(filter: { number: { eq: $number }, team: { key: { eq: $teamKey } } }) {
    nodes { id identifier }
  }
}
"""

_FILE_UPLOAD = """
mutation($contentType: String!, $filename: String!, $size: Int!) {
  fileUpload(contentType: $contentType, filename: $filename, size: $size) {
    success
    uploadFile { uploadUrl assetUrl headers { key value } }
  }
}
"""

_COMMENT_CREATE = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}
"""


def build_comment_markdown(ticket_key: str, report_url: str, score, verdict: str | None) -> str:
    score_txt = f"{score}" if score is not None else "n/a"
    verdict_txt = verdict or "see report"
    return (
        f"**QA Evidence — {ticket_key}**\n\n"
        f"- Verdict: **{verdict_txt}**\n"
        f"- Score: **{score_txt}**\n"
        f"- Full report: {report_url}\n\n"
        f"Evidence PDF attached above. Generated by SCRIBE auto-QA."
    )


def _headers(token: str) -> dict:
    return {"Authorization": token, "Content-Type": "application/json"}


async def resolve_issue_id(key: str, token: str) -> str | None:
    """INV-660 -> Linear issue UUID, via team-key + number filter."""
    try:
        team_key, num = key.split("-", 1)
        number = float(int(num))
    except Exception:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(LINEAR_API, headers=_headers(token), json={
                "query": _ISSUE_ID_QUERY,
                "variables": {"number": number, "teamKey": team_key},
            })
            if resp.status_code != 200:
                return None
            nodes = (((resp.json().get("data") or {}).get("issues") or {}).get("nodes") or [])
            return nodes[0]["id"] if nodes else None
    except Exception:
        return None


async def attach_evidence(ticket_key, pdf_path, comment_md, *, token, write_allowed):
    """Upload the PDF and post a comment. Gated; never raises."""
    if not write_allowed:
        return {"attached": False, "skipped_reason": "write permission off", "error": None}
    if not token:
        return {"attached": False, "skipped_reason": "no LINEAR_TOKEN", "error": None}
    if not os.path.exists(pdf_path):
        return {"attached": False, "skipped_reason": "pdf missing", "error": None}

    issue_id = await resolve_issue_id(ticket_key, token)
    if not issue_id:
        return {"attached": False, "skipped_reason": None, "error": f"could not resolve issue id for {ticket_key}"}

    try:
        data = open(pdf_path, "rb").read()
        filename = f"{ticket_key}-evidence.pdf"
        async with httpx.AsyncClient(timeout=60) as client:
            up = await client.post(LINEAR_API, headers=_headers(token), json={
                "query": _FILE_UPLOAD,
                "variables": {"contentType": "application/pdf", "filename": filename, "size": len(data)},
            })
            payload = (((up.json().get("data") or {}).get("fileUpload") or {}))
            uf = payload.get("uploadFile") or {}
            upload_url, asset_url = uf.get("uploadUrl"), uf.get("assetUrl")
            if not payload.get("success") or not upload_url or not asset_url:
                return {"attached": False, "skipped_reason": None, "error": "fileUpload failed"}

            put_headers = {h["key"]: h["value"] for h in (uf.get("headers") or [])}
            put_headers.setdefault("Content-Type", "application/pdf")
            put = await client.put(upload_url, headers=put_headers, content=data)
            if put.status_code not in (200, 201, 204):
                return {"attached": False, "skipped_reason": None, "error": f"asset PUT {put.status_code}"}

            body = f"[{ticket_key}-evidence.pdf]({asset_url})\n\n{comment_md}"
            cc = await client.post(LINEAR_API, headers=_headers(token), json={
                "query": _COMMENT_CREATE, "variables": {"issueId": issue_id, "body": body},
            })
            ok = (((cc.json().get("data") or {}).get("commentCreate") or {}).get("success"))
            if not ok:
                return {"attached": False, "skipped_reason": None, "error": "commentCreate failed"}
    except Exception as e:
        return {"attached": False, "skipped_reason": None, "error": str(e)}

    return {"attached": True, "skipped_reason": None, "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_linear_writer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/linear_writer.py backend/tests/test_linear_writer.py
git commit -m "feat(linear): write client — resolve id, upload PDF, post comment (gated)"
```

---

## Task 5: `qa_orchestrator.py` — run→report→pdf→(gated)attach + the gate

**Files:**
- Create: `backend/qa_orchestrator.py`
- Test: `backend/tests/test_qa_orchestrator.py`

**Interfaces:**
- Consumes: `qa_runner.run`, `agents.generate_html_report`, `agents.EVIDENCE_DIR`, `pdf_export.export`, `linear_writer.*`, `instance_config.load_instance_config`, `config.QA_EVIDENCE_MODEL`.
- Produces:
  - `compute_attach_gate(cfg: dict, *, armed: bool, manual: bool) -> bool` — `write_flag and (manual or armed)`.
  - `read_run_summary(ticket_key: str, run_name: str) -> dict` — best-effort `{score, verdict}` from `summary.json`.
  - `resolve_env_url(cfg: dict, env_url: str) -> str`
  - `async run_and_finalize(ticket_key: str, env_url: str, *, armed: bool, manual: bool = False, model: str | None = None) -> AsyncIterator[dict]` — full pipeline; terminal `{"type":"done", "success": bool, "report_url": str, "pdf": str|None, "attached": bool, "skipped_reason": str|None}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_qa_orchestrator.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_qa_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qa_orchestrator'`.

- [ ] **Step 3: Write the implementation**

Create `backend/qa_orchestrator.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_qa_orchestrator.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/qa_orchestrator.py backend/tests/test_qa_orchestrator.py
git commit -m "feat(orchestrator): run->report->pdf->gated-attach pipeline"
```

---

## Task 6: Backend endpoints — `/api/qa-run`, `/api/attach`, `/api/automation`

**Files:**
- Modify: `backend/server.py` (add request models + endpoints near `/api/test` at `:800-821`; auto-mode config endpoints).
- Test: extend `backend/tests/test_qa_orchestrator.py` is not enough — add `backend/tests/test_server_automation.py` using FastAPI TestClient.

**Interfaces:**
- Consumes: `qa_orchestrator.run_and_finalize`, `auto_mode` (Task 7 — guard import so this task stands alone), `streams`, `_run_stream`, `load_instance_config`, `pipeline_store`.
- Produces (HTTP):
  - `POST /api/qa-run/{key}` body `{envUrl?: str}` → `{streamId}`
  - `POST /api/attach/{key}` → `{streamId}` (manual attach of latest run)
  - `GET /api/automation` → `{writeAllowed, autoMode:{enabled, armed}}`
  - `POST /api/automation` body `{enabled?, armed?}` → updated state

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_server_automation.py`:
```python
from fastapi.testclient import TestClient
import server


def test_automation_get_returns_shape():
    client = TestClient(server.app)
    res = client.get("/api/automation")
    assert res.status_code == 200
    body = res.json()
    assert "writeAllowed" in body
    assert "autoMode" in body and "enabled" in body["autoMode"] and "armed" in body["autoMode"]


def test_automation_post_sets_state():
    client = TestClient(server.app)
    res = client.post("/api/automation", json={"enabled": True, "armed": False})
    assert res.status_code == 200
    assert res.json()["autoMode"]["enabled"] is True
    # reset
    client.post("/api/automation", json={"enabled": False, "armed": False})


def test_qa_run_returns_stream_id(monkeypatch):
    client = TestClient(server.app)
    res = client.post("/api/qa-run/INV-1", json={"envUrl": "https://x"})
    assert res.status_code == 200
    assert "streamId" in res.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_server_automation.py -v`
Expected: FAIL — 404 for `/api/automation` and `/api/qa-run/INV-1`.

- [ ] **Step 3: Add the endpoints**

In `backend/server.py`, near the other request models (`:785-797`), add:
```python
class QaRunRequest(BaseModel):
    envUrl: str = ""


class AutomationRequest(BaseModel):
    enabled: bool | None = None
    armed: bool | None = None
```

Add an import near the council import (`server.py:~84`):
```python
import qa_orchestrator
import auto_mode
auto_mode.configure(pipeline_store, streams)
```

After the `/api/test` endpoint (`:821`), add:
```python
@app.post("/api/qa-run/{key}")
async def api_qa_run(key: str, req: QaRunRequest):
    """Close the copy-paste gap (#9): run qa-evidence server-side, then report+pdf+gated attach."""
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    state = auto_mode.get_state()
    asyncio.create_task(_run_stream(
        stream_id,
        qa_orchestrator.run_and_finalize(key, req.envUrl, armed=state["armed"], manual=False),
    ))
    return {"streamId": stream_id}


@app.post("/api/attach/{key}")
async def api_attach(key: str):
    """Manual 'Attach to Linear' for the latest run — write-flag only, no arm needed."""
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, auto_mode.attach_latest(key)))
    return {"streamId": stream_id}


@app.get("/api/automation")
async def api_automation_get():
    cfg = load_instance_config() or {}
    write_allowed = bool((cfg.get("issueTracker") or {}).get("access", {}).get("write", False))
    return {"writeAllowed": write_allowed, "autoMode": auto_mode.get_state()}


@app.post("/api/automation")
async def api_automation_set(req: AutomationRequest):
    auto_mode.set_state(enabled=req.enabled, armed=req.armed)
    cfg = load_instance_config() or {}
    write_allowed = bool((cfg.get("issueTracker") or {}).get("access", {}).get("write", False))
    return {"writeAllowed": write_allowed, "autoMode": auto_mode.get_state()}
```

> Note: this task imports `auto_mode` (Task 7). Implement Task 7 first if executing strictly in order, OR create a minimal `auto_mode.py` stub with `configure/get_state/set_state/attach_latest/run_loop` then flesh it out in Task 7. Recommended: **do Task 7 before Task 6's test run.**

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_server_automation.py -v`
Expected: 3 passed (after Task 7's `auto_mode` exists).

- [ ] **Step 5: Commit**

```bash
git add backend/server.py backend/tests/test_server_automation.py
git commit -m "feat(api): /api/qa-run, /api/attach, /api/automation endpoints"
```

---

## Task 7: `auto_mode.py` — state + background loop (#10)

**Files:**
- Create: `backend/auto_mode.py`
- Test: `backend/tests/test_auto_mode.py`

**Interfaces:**
- Consumes: `pipeline_store.get_meta/set_meta`, `streams`, `qa_orchestrator.run_and_finalize`, `linear_writer`, `agents`, `instance_config`, `config`, `linear_client.get_tickets`.
- Produces:
  - `configure(store, streams_registry) -> None`
  - `get_state() -> {"enabled": bool, "armed": bool}`
  - `set_state(*, enabled: bool | None = None, armed: bool | None = None) -> None`
  - `eligible_tickets(tickets: list[dict]) -> list[dict]`
  - `async attach_latest(ticket_key: str) -> AsyncIterator[dict]`
  - `async run_loop() -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auto_mode.py`:
```python
import auto_mode


class _FakeStore:
    def __init__(self): self._m = {}
    def get_meta(self, k, default=None): return self._m.get(k, default)
    def set_meta(self, k, v): self._m[k] = v


def test_state_roundtrip():
    auto_mode.configure(_FakeStore(), None)
    assert auto_mode.get_state() == {"enabled": False, "armed": False}
    auto_mode.set_state(enabled=True)
    assert auto_mode.get_state()["enabled"] is True
    assert auto_mode.get_state()["armed"] is False
    auto_mode.set_state(armed=True)
    assert auto_mode.get_state() == {"enabled": True, "armed": True}


def test_eligible_filters_and_sorts():
    tickets = [
        {"key": "INV-3", "statusCategory": "in_qa", "priority": "High"},
        {"key": "INV-1", "statusCategory": "ready_for_qa", "priority": "Low"},
        {"key": "INV-2", "statusCategory": "ready_for_qa", "priority": "Highest"},
    ]
    out = auto_mode.eligible_tickets(tickets)
    assert [t["key"] for t in out] == ["INV-2", "INV-1"]  # only ready_for_qa, priority desc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_auto_mode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auto_mode'`.

- [ ] **Step 3: Write the implementation**

Create `backend/auto_mode.py`:
```python
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
    cur = get_state()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if armed is not None:
        cur["armed"] = bool(armed)
    if _store:
        _store.set_meta(_STATE_KEY, cur)


def eligible_tickets(tickets: list[dict]) -> list[dict]:
    ready = [t for t in tickets if t.get("statusCategory") == "ready_for_qa"]
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
    state = get_state()
    stream_id = str(uuid.uuid4())
    stream = _streams.create(stream_id) if _streams else None
    _active.add(ticket_key)
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
                    for t in tickets:
                        from server import check_evidence  # categorize + evidence (live import avoids cycle)
                else:
                    tickets = []
                # categorize so eligible_tickets can filter
                from status_map import categorize_status, resolve_status_mapping
                mapping = resolve_status_mapping(cfg, issue.get("type") or "jira")
                for t in tickets:
                    t["statusCategory"] = categorize_status(t.get("status", ""), mapping)
                for t in eligible_tickets(tickets):
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
```

> Note: trim the unused `check_evidence` import if your linter flags it — the loop only needs `categorize_status`/`resolve_status_mapping`. Keep `eligible_tickets` filtering on `statusCategory` exactly as the test expects.

- [ ] **Step 4: Register the loop in startup**

In `backend/server.py`, inside the existing `@app.on_event("startup")` handler (`_start_auto_provision_loop`, `:99-110`), add after the existing `asyncio.create_task(...)` lines (do NOT gate auto-mode on env mode — it should run for deployed apps):
```python
    asyncio.create_task(auto_mode.run_loop())
```
Place it so it runs regardless of the early `return` for already-deployed modes — i.e. add a second startup handler to keep it independent:
```python
@app.on_event("startup")
async def _start_auto_mode_loop():
    asyncio.create_task(auto_mode.run_loop())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_auto_mode.py tests/test_server_automation.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add backend/auto_mode.py backend/tests/test_auto_mode.py backend/server.py
git commit -m "feat(#10): auto_mode state + background loop, registered in startup"
```

---

## Task 8: Frontend API functions

**Files:**
- Modify: `frontend/src/api.ts` (add near `startTest` at `:111-120`).

**Interfaces:**
- Produces: `startQaRun`, `attachToLinear`, `getAutomation`, `setAutomation`.

- [ ] **Step 1: Add the API functions**

In `frontend/src/api.ts`, add:
```ts
export async function startQaRun(ticketKey: string, envUrl = ''): Promise<string> {
  const res = await fetch(`${BASE}/qa-run/${ticketKey}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ envUrl }),
  })
  if (!res.ok) throw new Error(`Failed to start QA run: ${res.status}`)
  return (await res.json()).streamId
}

export async function attachToLinear(ticketKey: string): Promise<string> {
  const res = await fetch(`${BASE}/attach/${ticketKey}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to start attach: ${res.status}`)
  return (await res.json()).streamId
}

export interface AutomationState {
  writeAllowed: boolean
  autoMode: { enabled: boolean; armed: boolean }
}

export async function getAutomation(): Promise<AutomationState> {
  const res = await fetch(`${BASE}/automation`)
  if (!res.ok) throw new Error(`getAutomation failed: ${res.status}`)
  return res.json()
}

export async function setAutomation(patch: { enabled?: boolean; armed?: boolean }): Promise<AutomationState> {
  const res = await fetch(`${BASE}/automation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`setAutomation failed: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(api-client): qa-run, attach, automation functions"
```

---

## Task 9: LaneCard — "Run QA" + "Attach to Linear" buttons (#9 UI)

**Files:**
- Modify: `frontend/src/components/LaneCard.tsx` (add props + buttons), `frontend/src/App.tsx` (handlers).

**Interfaces:**
- Consumes: `startQaRun`, `attachToLinear`, `subscribeSSE`, `getAutomation` (for `writeAllowed`).
- Produces: lane behavior — Run QA streams into the lane; on `done.success`, the existing report URL refresh applies.

- [ ] **Step 1: Add handler props to LaneCard**

In `frontend/src/components/LaneCard.tsx`, extend `Props` (after `onStartFromQuartermaster`):
```tsx
  onRunQa: (laneId: string) => void
  onAttachLinear: (laneId: string) => void
  writeAllowed?: boolean
```
And destructure them in the component signature:
```tsx
export default function LaneCard({ lane, onCancel, onCheckEvidence, onCheckDeploy, onRunCommand, onGenerateReport, onResume, onOverrideCouncil, onStartFromQuartermaster, onRunQa, onAttachLinear, writeAllowed = false, needsBuildDeploy = true }: Props) {
```

- [ ] **Step 2: Add the Run QA + Attach buttons**

In `LaneCard.tsx`, replace the qaCommand block (`:176-188`, the "Run this in Claude Code:" `<div>`) with a primary Run QA action plus the demoted copy fallback:
```tsx
      {lane.qaCommand && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
            <button className="btn btn--primary btn--small" onClick={() => onRunQa(lane.id)}
                    title="Run qa-evidence server-side (no terminal needed)">
              Run QA
            </button>
            {reportUrl && writeAllowed && (
              <button className="btn btn--secondary btn--small" onClick={() => onAttachLinear(lane.id)}
                      title="Attach the evidence PDF + comment to this Linear issue">
                Attach to Linear
              </button>
            )}
          </div>
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--muted, #9aa3af)' }}>
              copy command (fallback)
            </summary>
            <code
              onClick={() => navigator.clipboard.writeText(lane.qaCommand!)}
              title="Click to copy"
              style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: 'var(--bg, #15171c)', border: '1px solid var(--border, #2c313a)', borderRadius: 6, padding: '8px 10px', fontSize: 12, cursor: 'pointer', userSelect: 'all', marginTop: 4 }}
            >
              {lane.qaCommand}
            </code>
          </details>
        </div>
      )}
```

- [ ] **Step 3: Add handlers in App.tsx + pass props**

In `frontend/src/App.tsx`, add imports: `import { startQaRun, attachToLinear, getAutomation } from './api'` (merge into the existing api import). Add a `writeAllowed` state + load it:
```tsx
  const [writeAllowed, setWriteAllowed] = useState(false)
  useEffect(() => {
    getAutomation().then(a => setWriteAllowed(a.writeAllowed)).catch(() => {})
  }, [])
```
Add handlers (near `handleStart`), reusing the existing SSE wiring shape:
```tsx
  const handleRunQa = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return
    laneCurrentAgent.current[laneId] = 'inspector'
    updateLaneAgent(laneId, 'inspector', { state: 'active', progress: 10, message: 'Running QA server-side…' })
    try {
      const streamId = await startQaRun(lane.ticket.key, lane.env || '')
      const cleanup = subscribeSSE(streamId, (event) => {
        if (event.type === 'log') { appendLog(laneId, event.data ?? ''); updateLaneAgent(laneId, 'inspector', { message: event.data ?? '' }) }
        else if (event.type === 'progress') updateLaneAgent(laneId, 'inspector', { progress: event.pct ?? 0, eta: event.eta ?? '' })
        else if (event.type === 'done') {
          const d = event as unknown as { success?: boolean; report_url?: string }
          if (d.success) {
            updateLaneAgent(laneId, 'inspector', { state: 'done', progress: 100, message: 'QA complete' })
            setLanes(prev => prev.map(l => l.id === laneId ? { ...l, reportUrl: d.report_url || l.reportUrl } : l))
          } else {
            updateLaneAgent(laneId, 'inspector', { state: 'failed', message: 'QA run failed — see log' })
          }
        }
      }, () => updateLaneAgent(laneId, 'inspector', { state: 'failed', message: 'Connection lost' }))
      sseCleanups.current[laneId] = cleanup
    } catch (err) {
      updateLaneAgent(laneId, 'inspector', { state: 'failed', message: String(err) })
    }
  }, [lanes])

  const handleAttachLinear = useCallback(async (laneId: string) => {
    const lane = lanes.find(l => l.id === laneId)
    if (!lane) return
    try {
      const streamId = await attachToLinear(lane.ticket.key)
      subscribeSSE(streamId, (event) => {
        if (event.type === 'log') appendLog(laneId, event.data ?? '')
        else if (event.type === 'done') {
          const d = event as unknown as { attached?: boolean; skipped_reason?: string }
          appendLog(laneId, d.attached ? 'Attached to Linear ✓' : `Not attached: ${d.skipped_reason || 'error'}`)
        }
      }, () => {})
    } catch (err) {
      appendLog(laneId, `Attach failed: ${err}`)
    }
  }, [lanes])
```
Pass them where `<LaneCard ... />` is rendered (in `ActiveLanes`/App): add `onRunQa={handleRunQa} onAttachLinear={handleAttachLinear} writeAllowed={writeAllowed}`. (Find the `LaneCard` render site and the `ActiveLanes` props; thread the three new props through `ActiveLanes` if LaneCard is rendered there.)

- [ ] **Step 4: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds. Manual: start a deployed-mode ticket into a lane; at the Test stage you now see **Run QA** (primary) and a collapsed "copy command (fallback)". Clicking Run QA streams log lines into the lane and ends with "QA complete" + a View Report link. **This is #9 demoed.**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LaneCard.tsx frontend/src/App.tsx
git commit -m "feat(#9): Run QA + Attach to Linear buttons (server-side, no copy-paste)"
```

---

## Task 10: TopBar — Auto Mode controls (#10 UI)

**Files:**
- Modify: `frontend/src/components/TopBar.tsx` (add controls in `top-bar__actions`), `frontend/src/App.tsx` (pass automation state/handlers).

**Interfaces:**
- Consumes: `getAutomation`, `setAutomation`.

- [ ] **Step 1: Add props + controls to TopBar**

In `TopBar.tsx`, extend `Props`:
```tsx
  autoMode: { enabled: boolean; armed: boolean }
  writeAllowed: boolean
  onToggleAutoMode: (enabled: boolean) => void
  onToggleArm: (armed: boolean) => void
```
Destructure them, and add to `top-bar__actions` (before the Daily Huddle button):
```tsx
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
                 title="Continuously QA Ready-for-QA tickets in the background">
            <input type="checkbox" checked={autoMode.enabled}
                   onChange={e => onToggleAutoMode(e.target.checked)} />
            Auto Mode
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4,
                          cursor: writeAllowed ? 'pointer' : 'not-allowed',
                          color: autoMode.armed ? 'var(--warning, #f5a524)' : 'var(--text-dim)' }}
                 title={writeAllowed ? 'When ON, auto mode attaches evidence to the live Linear board'
                                     : 'Write permission is off for this instance'}>
            <input type="checkbox" checked={autoMode.armed} disabled={!writeAllowed}
                   onChange={e => {
                     if (e.target.checked && !window.confirm('Arm auto-publish? Auto mode will attach evidence to the LIVE Linear board.')) return
                     onToggleArm(e.target.checked)
                   }} />
            Auto-publish
          </label>
        </span>
```

- [ ] **Step 2: Wire it in App.tsx**

In `App.tsx`, add automation state and handlers, then pass to `<TopBar />`:
```tsx
  const [autoMode, setAutoMode] = useState<{ enabled: boolean; armed: boolean }>({ enabled: false, armed: false })
  useEffect(() => {
    getAutomation().then(a => { setAutoMode(a.autoMode); setWriteAllowed(a.writeAllowed) }).catch(() => {})
  }, [])
  const handleToggleAutoMode = useCallback(async (enabled: boolean) => {
    const a = await setAutomation({ enabled }); setAutoMode(a.autoMode)
  }, [])
  const handleToggleArm = useCallback(async (armed: boolean) => {
    const a = await setAutomation({ armed }); setAutoMode(a.autoMode)
  }, [])
```
On the `<TopBar ... />` render, add:
```tsx
        autoMode={autoMode}
        writeAllowed={writeAllowed}
        onToggleAutoMode={handleToggleAutoMode}
        onToggleArm={handleToggleArm}
```
(Remove the duplicate `writeAllowed` init from Task 9 if it now lives in this combined effect — keep a single `getAutomation()` effect.)

- [ ] **Step 3: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds. Manual: header shows an **Auto Mode** checkbox and a disabled-unless-write **Auto-publish** checkbox; toggling Auto Mode hits `/api/automation` (verify in network tab); arming prompts a confirm. Leave Auto-publish OFF for the demo.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/TopBar.tsx frontend/src/App.tsx
git commit -m "feat(#10): Auto Mode + Auto-publish controls in header"
```

---

## Task 11: Full test sweep + integration smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all new tests pass; pre-existing failures limited to the known asyncio-subprocess WinError 6 chat/council cases (per project notes) — confirm no NEW failures.

- [ ] **Step 2: Frontend build**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Single-ticket end-to-end (auto-publish OFF)**

Restart the backend (`./start.sh` or the venv uvicorn command). In the UI, start one Beeventory ticket → click **Run QA** → confirm the lane streams progress, a report is generated, and the log says "Not published to Linear (gate closed)". Verify a `evidence.pdf` exists under `~/evidence/{key}/runs/{run}/`.

- [ ] **Step 4: Linear attach spike (throwaway issue)**

Temporarily set the instance `issueTracker.access.write` true (it already is) and arm Auto-publish, OR click **Attach to Linear** on a completed run against a **disposable test issue** (not a live client ticket). Confirm the PDF attaches and a comment posts. This validates the `fileUpload`→PUT→`commentCreate` spike. Then **disarm Auto-publish** for the demo.

- [ ] **Step 5: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "test(cluster-c): full sweep + integration smoke notes"
```

---

## Self-review notes (author)

- **Spec coverage:** #9 → Tasks 3,6,9. #10 → Tasks 5,7,10. #4 → Task 1. PDF → Task 2. Linear attach + gate → Tasks 4,5. Manual attach → Tasks 6,7,9. Double-gate + never-move-status → enforced in `compute_attach_gate` (no status-transition code anywhere). All spec sections map to a task.
- **Type consistency:** the orchestrator terminal event `{"type":"done", success, report_url, pdf, attached, skipped_reason, error}` is produced identically in `qa_orchestrator.run_and_finalize` and consumed in App.tsx `handleRunQa`. `auto_mode.get_state()` shape `{enabled, armed}` matches the `/api/automation` response and the TopBar prop.
- **Known risks (validate early):** (1) long unattended headless QA reliability — Task 11 Step 3 is the gate; (2) Linear `fileUpload`/issue-id flow — Task 11 Step 4 is the spike. Both are isolated so a failure there doesn't block the rest of the cluster.
