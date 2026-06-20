# Per-Task Model Switching (Cost Control) — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design)
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)

## Goal

Reduce Anthropic API cost by running SCRIBE's **low-effort** AI tasks on a cheaper
model (Haiku 4.5) while leaving the **high-effort** Code Reviewer untouched. The
change is intentionally minimal-risk: a `--model` flag added to only the two
downgraded tasks, sourced from one env-overridable config spot.

## Effort categorization

Today all three AI tasks spawn the **same** `claude -p` CLI subprocess with **no
`--model` flag**, so they share the CLI's default model. Ranked by AI effort:

| Task | File | Effort | Why | Model after change |
|---|---|---|---|---|
| **Code Reviewer** | `backend/council.py` | **HIGH** | Reasons over PR diffs up to **80K chars** (`_MAX_DIFF_CHARS`) for subtle correctness / security bugs + pattern audit | **unchanged** (CLI default) |
| **QA Evidence Reviewer** | `backend/council.py` | **LOW** | ~0.5K–1K token structured artifacts (summary.json, scores, screenshots) checked against explicit pass/fail criteria | `claude-haiku-4-5` |
| **FRIDAY Chat** | `backend/chat.py` | **VARIABLE** | Open-ended interactive chat; not QA-critical | `claude-haiku-4-5` |

**Decision:** downgrade **QA Evidence Reviewer** and **FRIDAY Chat**; leave
**Code Reviewer** exactly as-is (no flag added → byte-for-byte unchanged behavior).

## Mechanism

The `claude` CLI accepts `--model <id>`. We add that flag **only** to the two
downgraded tasks. The model id comes from a central config block in
`backend/config.py`, overridable per-deployment via environment variables. No model
is hardcoded at a call site; Code Reviewer never receives the flag.

## Changes — 3 files

### 1. `backend/config.py` — central model config

Add (matches the file's existing `os.environ.get(..., default)` convention):

```python
# --- Per-task model selection (cost control) ---
# Low-effort AI tasks run on a cheaper model to cut per-token API cost. The
# high-effort Code Reviewer is intentionally NOT pinned here — it keeps the CLI
# default model. Override either via env var without a code change.
CHEAP_MODEL = "claude-haiku-4-5"
QA_EVIDENCE_MODEL = os.environ.get("SCRIBE_QA_EVIDENCE_MODEL", CHEAP_MODEL)
CHAT_MODEL        = os.environ.get("SCRIBE_CHAT_MODEL", CHEAP_MODEL)
```

### 2. `backend/council.py`

- Add an optional field to the `Reviewer` dataclass:
  ```python
  model: Optional[str] = None
  ```
- `_build_reviewer_cmd(prompt, model=None)` inserts `["--model", model]` into the
  argv **only when `model` is truthy**:
  ```python
  def _build_reviewer_cmd(prompt: str, model: Optional[str] = None) -> list[str]:
      cmd = [_claude_bin(), "-p", "--output-format", "stream-json",
             "--verbose", "--permission-mode", "bypassPermissions"]
      if model:
          cmd += ["--model", model]
      cmd.append(prompt)
      return cmd
  ```
- `_run_reviewer` passes `reviewer.model` into the builder:
  `cmd = _build_reviewer_cmd(prompt, reviewer.model)`.
- `_default_reviewers()` sets the model on the **qa-evidence** reviewer only:
  ```python
  from config import QA_EVIDENCE_MODEL
  return [
      Reviewer(name="qa-evidence", prompt_builder=build_qa_evidence_prompt,
               model=QA_EVIDENCE_MODEL),
      Reviewer(name="code-reviewer", prompt_builder=build_code_reviewer_prompt),
      # code-reviewer: model defaults to None → no --model flag → CLI default
  ]
  ```

### 3. `backend/chat.py`

- Import the chat model and inject the flag in `_build_cmd` (guarded so an empty
  value adds nothing):
  ```python
  from config import CHAT_MODEL
  ...
  parts = [CLAUDE_BIN, "-p", "--output-format", "stream-json",
           "--verbose", "--permission-mode", "bypassPermissions"]
  if CHAT_MODEL:
      parts += ["--model", CHAT_MODEL]
  if session_id:
      parts += ["--resume", session_id]
  parts.append(message)
  ```

## Important precondition (cost caveat)

Switching to Haiku **only saves dollars when SCRIBE runs on an `ANTHROPIC_API_KEY`
(per-token billing)**. Under Claude subscription auth, it changes quota usage and
latency but **not** direct dollar cost. SCRIBE's `.secrets.env` can hold a key
(loaded into `os.environ` at backend startup). Confirm which auth mode is active
before claiming dollar savings; the change is still valid (speed / quota) either way.

## Verification

- **Unit:**
  - `_build_reviewer_cmd(prompt, "claude-haiku-4-5")` contains `--model
    claude-haiku-4-5`; `_build_reviewer_cmd(prompt)` (or `None`) contains **no**
    `--model`.
  - `_default_reviewers()` yields `qa-evidence` with `model == QA_EVIDENCE_MODEL`
    and `code-reviewer` with `model is None`.
  - chat `_build_cmd(...)` includes `--model <CHAT_MODEL>`.
- **Live smoke:** run a council on a known ticket — qa-evidence still returns a
  `VERDICT:` line and the code-reviewer's output/behavior is unchanged; send one
  chat message and confirm a reply streams back.

## Escape hatch

If Haiku proves too weak for the evidence gate, set `SCRIBE_QA_EVIDENCE_MODEL` to a
stronger id (e.g. `claude-sonnet-4-6`) in the deployment's env — no code change, no
logic redeploy. Same for `SCRIBE_CHAT_MODEL`.

## Out of scope

- Anthropic SDK migration (still using `claude -p` subprocesses).
- Per-instance / UI-driven model configuration (`instance.config.json` schema).
- Changing the Code Reviewer's model.
- Dynamic per-request model selection by input size.
