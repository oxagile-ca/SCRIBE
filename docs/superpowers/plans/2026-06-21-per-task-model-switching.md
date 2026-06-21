# Per-Task Model Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut AI cost by running the two low-effort tasks (QA-Evidence reviewer + FRIDAY chat) on a cheap model (`claude-haiku-4-5`) while leaving the high-effort Code Reviewer on the CLI default model.

**Architecture:** All three AI tasks spawn the `claude -p` CLI. We add a `--model` flag **only** to the two downgraded tasks, sourcing the model id from one central, env-overridable spot in `backend/config.py`. The Code Reviewer's command is never touched, so its behavior is byte-for-byte unchanged.

**Tech Stack:** Python 3.12 + pytest (backend). No new dependencies.

## Global Constraints

- Cheap model id: **`claude-haiku-4-5`** (CLI alias; the dated form `claude-haiku-4-5-20251001` also resolves). This is the default value of the new config constants.
- **Code Reviewer stays untouched** — it must receive NO `--model` flag (keeps the CLI/subscription default).
- Model ids are env-overridable: **`SCRIBE_QA_EVIDENCE_MODEL`** and **`SCRIBE_CHAT_MODEL`** (the escape hatch — flip back to a stronger model with no code change).
- Run backend tests with **python3.12** (`py -3.12 -m pytest ...` on this Windows machine; fall back to `python3.12`/`python`).
- **Backward compatibility:** the existing test `test_build_reviewer_cmd_is_argv_with_prompt_intact` (calls `_build_reviewer_cmd(prompt)` with no model) MUST still pass — the new `model` parameter defaults to `None` and adds nothing when unset.
- **Integration note (composes with the usage-tracking feature already on this branch):** when `--model claude-haiku-4-5` is passed, the `claude -p` stream's `system` init event reports that model, which `usage_ledger.parse_model_from_init` already captures — so the usage ledger will record the QA-Evidence row as `claude-haiku-4-5` after this change. No extra wiring needed; do not duplicate model-capture logic.

---

## File Structure

- **Modify** `backend/config.py` — add `CHEAP_MODEL`, `QA_EVIDENCE_MODEL`, `CHAT_MODEL` constants.
- **Modify** `backend/council.py` — add `model` field to `Reviewer`; add optional `model` param to `_build_reviewer_cmd`; thread `reviewer.model` through `_run_reviewer`; set the model on the qa-evidence reviewer in `_default_reviewers`.
- **Modify** `backend/chat.py` — import `CHAT_MODEL`; inject `--model` into `_build_cmd` when set.
- **Modify** `backend/tests/test_council.py` — model-flag + reviewer-assignment tests.
- **Create** `backend/tests/test_model_config.py` — config default + env-override tests.
- **Modify** `backend/tests/test_chat.py` — chat `_build_cmd` model-flag tests.

---

### Task 1: Central model config (`config.py`)

**Files:**
- Modify: `backend/config.py` (add a model-selection block near the other env-driven settings)
- Create: `backend/tests/test_model_config.py`

**Interfaces:**
- Produces: `config.CHEAP_MODEL: str`, `config.QA_EVIDENCE_MODEL: str`, `config.CHAT_MODEL: str` (defaults all `"claude-haiku-4-5"`; the latter two overridable via `SCRIBE_QA_EVIDENCE_MODEL` / `SCRIBE_CHAT_MODEL`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_model_config.py
import importlib


def test_model_defaults():
    import config
    importlib.reload(config)  # ensure no leftover env override from another test
    assert config.CHEAP_MODEL == "claude-haiku-4-5"
    assert config.QA_EVIDENCE_MODEL == "claude-haiku-4-5"
    assert config.CHAT_MODEL == "claude-haiku-4-5"


def test_model_env_override(monkeypatch):
    import config
    monkeypatch.setenv("SCRIBE_QA_EVIDENCE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("SCRIBE_CHAT_MODEL", "claude-opus-4-8")
    importlib.reload(config)
    try:
        assert config.QA_EVIDENCE_MODEL == "claude-sonnet-4-6"
        assert config.CHAT_MODEL == "claude-opus-4-8"
    finally:
        # restore defaults so later tests see the unoverridden module
        monkeypatch.undo()
        importlib.reload(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && py -3.12 -m pytest tests/test_model_config.py -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'CHEAP_MODEL'`.

- [ ] **Step 3: Add the config block**

In `backend/config.py`, add near the other `os.environ.get(...)` settings (e.g. after the `JIRA_EMAIL`/`JIRA_TOKEN` lines):

```python
# --- Per-task model selection (cost control) ---
# Low-effort AI tasks (QA-Evidence reviewer, FRIDAY chat) run on a cheaper model to
# cut per-token API cost. The high-effort Code Reviewer is intentionally NOT pinned
# here — it keeps the CLI default model. Override either via env var, no code change.
CHEAP_MODEL = "claude-haiku-4-5"
QA_EVIDENCE_MODEL = os.environ.get("SCRIBE_QA_EVIDENCE_MODEL", CHEAP_MODEL)
CHAT_MODEL = os.environ.get("SCRIBE_CHAT_MODEL", CHEAP_MODEL)
```

(`config.py` already does `import os` at the top — confirm it's present; do not add a duplicate.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && py -3.12 -m pytest tests/test_model_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_model_config.py
git commit -m "feat(model-switch): central per-task model config (env-overridable)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Council reviewer model wiring (`council.py`)

**Files:**
- Modify: `backend/council.py` (`Reviewer` dataclass; `_build_reviewer_cmd`; the `cmd = _build_reviewer_cmd(...)` call in `_run_reviewer`; `_default_reviewers`)
- Modify: `backend/tests/test_council.py`

**Interfaces:**
- Consumes: `config.QA_EVIDENCE_MODEL` (Task 1).
- Produces: `Reviewer.model: Optional[str] = None`; `_build_reviewer_cmd(prompt: str, model: Optional[str] = None) -> list[str]` (adds `["--model", model]` only when `model` is truthy); `_default_reviewers()` returns qa-evidence with `model=QA_EVIDENCE_MODEL` and code-reviewer with `model=None`.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/test_council.py
def test_build_reviewer_cmd_includes_model_when_set():
    from council import _build_reviewer_cmd
    cmd = _build_reviewer_cmd("PROMPT TEXT", "claude-haiku-4-5")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"
    assert cmd[-1] == "PROMPT TEXT"   # prompt stays the final argv element


def test_build_reviewer_cmd_omits_model_when_none():
    from council import _build_reviewer_cmd
    cmd = _build_reviewer_cmd("PROMPT TEXT")
    assert "--model" not in cmd
    assert cmd[-1] == "PROMPT TEXT"


def test_default_reviewers_sets_model_only_on_qa_evidence():
    import config
    from council import _default_reviewers
    by_name = {r.name: r for r in _default_reviewers()}
    assert by_name["qa-evidence"].model == config.QA_EVIDENCE_MODEL
    assert by_name["code-reviewer"].model is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && py -3.12 -m pytest tests/test_council.py -k "model" -v`
Expected: FAIL — `_build_reviewer_cmd()` takes 1 positional arg (model param doesn't exist yet) / `Reviewer` has no `model` attribute.

- [ ] **Step 3: Implement the wiring**

In `backend/council.py`:

(a) Add a field to the `Reviewer` dataclass (it already imports `Optional`):

```python
@dataclass
class Reviewer:
    name: str
    prompt_builder: Callable[..., str]
    idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S
    total_timeout_s: int = DEFAULT_TOTAL_TIMEOUT_S
    model: Optional[str] = None
```

(b) Replace `_build_reviewer_cmd` so it accepts and injects the model:

```python
def _build_reviewer_cmd(prompt: str, model: Optional[str] = None) -> list[str]:
    """Argv list for create_subprocess_exec. A `--model` flag is added only when
    `model` is set; the prompt is always the final element."""
    cmd = [
        _claude_bin(),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    return cmd
```

(c) In `_run_reviewer`, pass the reviewer's model into the builder (the call currently reads `cmd = _build_reviewer_cmd(prompt)`):

```python
    cmd = _build_reviewer_cmd(prompt, reviewer.model)
```

(d) In `_default_reviewers`, set the model on the qa-evidence reviewer only:

```python
def _default_reviewers() -> list:
    from council_prompts import build_qa_evidence_prompt, build_code_reviewer_prompt
    from config import QA_EVIDENCE_MODEL
    return [
        Reviewer(name="qa-evidence", prompt_builder=build_qa_evidence_prompt,
                 model=QA_EVIDENCE_MODEL),
        Reviewer(name="code-reviewer", prompt_builder=build_code_reviewer_prompt),
        # code-reviewer: model stays None → no --model flag → CLI default
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && py -3.12 -m pytest tests/test_council.py -v`
Expected: PASS — the three new tests pass AND the pre-existing `test_build_reviewer_cmd_is_argv_with_prompt_intact` still passes (model defaults to None). The unrelated `WinError` subprocess tests (`test_run_reviewer_*`) remain the documented pre-existing Windows failures — not introduced here.

- [ ] **Step 5: Commit**

```bash
git add backend/council.py backend/tests/test_council.py
git commit -m "feat(model-switch): route QA-Evidence reviewer to cheap model via --model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Chat model flag (`chat.py`)

**Files:**
- Modify: `backend/chat.py` (import `CHAT_MODEL`; `_build_cmd`)
- Modify: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: `config.CHAT_MODEL` (Task 1).
- Produces: `_build_cmd` includes `--model <CHAT_MODEL>` when `CHAT_MODEL` is truthy.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/test_chat.py
def test_build_cmd_includes_model(monkeypatch):
    import chat
    monkeypatch.setattr(chat, "CHAT_MODEL", "claude-haiku-4-5")
    cmd = chat._build_cmd("hello", None)
    assert "--model" in cmd
    assert "claude-haiku-4-5" in cmd


def test_build_cmd_omits_model_when_empty(monkeypatch):
    import chat
    monkeypatch.setattr(chat, "CHAT_MODEL", "")
    cmd = chat._build_cmd("hello", None)
    assert "--model" not in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && py -3.12 -m pytest tests/test_chat.py -k "model" -v`
Expected: FAIL — `chat` has no attribute `CHAT_MODEL` (not imported yet) / no `--model` in the built command.

- [ ] **Step 3: Implement**

In `backend/chat.py`:

(a) Import the chat model near the top (after the existing imports, e.g. below `import usage_ledger`):

```python
from config import CHAT_MODEL
```

(b) Inject the flag in `_build_cmd` (guarded so an empty value adds nothing):

```python
def _build_cmd(message: str, session_id: Optional[str]) -> str:
    parts = [
        CLAUDE_BIN,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]
    if CHAT_MODEL:
        parts += ["--model", CHAT_MODEL]
    if session_id:
        parts += ["--resume", session_id]
    parts.append(message)
    return " ".join(shlex.quote(p) for p in parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && py -3.12 -m pytest tests/test_chat.py -k "model" -v`
Expected: PASS (2 tests). The pre-existing chat subprocess `WinError` tests are unaffected (and remain the documented Windows-only failures).

- [ ] **Step 5: Commit**

```bash
git add backend/chat.py backend/tests/test_chat.py
git commit -m "feat(model-switch): route FRIDAY chat to cheap model via --model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Cheap model for QA-Evidence + Chat, Code Reviewer untouched → Task 2 (qa-evidence model set, code-reviewer None) + Task 3 (chat). ✓
- Central, env-overridable config → Task 1 (`SCRIBE_QA_EVIDENCE_MODEL`/`SCRIBE_CHAT_MODEL`). ✓
- `--model` flag added only to downgraded tasks; Code Reviewer byte-for-byte unchanged → Task 2 `if model:` guard + code-reviewer `model=None`. ✓
- Escape hatch (revert via env) → Task 1 override test. ✓
- Composes with usage ledger (model captured from stream) → Global Constraints integration note; no code needed. ✓
- Verification (unit on cmd builders; live smoke) → unit tests in Tasks 2 & 3; live smoke is post-merge (run a council and confirm the ledger row reads `claude-haiku-4-5` for qa-evidence). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; all tests are concrete.

**3. Type consistency:** `_build_reviewer_cmd(prompt, model=None)` signature matches its call in `_run_reviewer` and the Task 2 tests. `Reviewer.model` (Optional[str]) is set in `_default_reviewers` and read in `_run_reviewer`. `config.QA_EVIDENCE_MODEL`/`config.CHAT_MODEL`/`config.CHEAP_MODEL` names are identical across Tasks 1, 2, 3 and their tests. `chat.CHAT_MODEL` import name matches the monkeypatch target in Task 3 tests.

---

## Execution notes

- **Order:** Task 1 (config) must precede Tasks 2 and 3 (both import the config constants). Tasks 2 and 3 are independent of each other.
- **Branch:** build on a branch stacked on `feat/usage-tracking` (e.g. `feat/model-switching`) so the demo shows the usage ledger recording the model flip Opus→Haiku. Commit per task; do not push/merge without the user's go-ahead.
- **Live smoke after merge:** run **Check Evidence** on a ticket and confirm the new ledger row reads `qa-evidence · claude-haiku-4-5` (down from `claude-opus-4-8`), with a correspondingly lower cost; code-reviewer stays on the default model.
