# Test Cases on the Card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a `Test cases (N)` button on both the Queue row and the Active-lane card that opens one shared modal showing the ticket's own test cases (read-only) and letting the user add, edit, and delete their own on top.

**Architecture:** The store and three endpoints already exist and already feed real QA runs. This adds one backend function + one `PATCH` route for editing, extracts the ticket-description parser into a pure testable module, and builds a self-contained `TestCasesModal` leaf component mounted by each card. The Queue row's existing inline test-case block is removed — the modal replaces it, not duplicates it.

**Tech Stack:** FastAPI + pytest (backend, `~/SCRIBE/backend`); React 18 + TypeScript + Vite (frontend, `~/SCRIBE/frontend`); frontend tests are framework-free TypeScript bundled by esbuild and run by node.

**Spec:** `docs/superpowers/specs/2026-07-20-test-cases-card-design.md`

## Global Constraints

- Branch: `feat/application-profile` in `~/SCRIBE`. Do not create a new branch.
- Backend commands run from `~/SCRIBE` using the repo venv: `.venv/Scripts/python.exe -m pytest ...` (Windows). Plain `python` is a different interpreter with no pytest.
- Frontend commands run from `~/SCRIBE/frontend`.
- Added test cases are **local to VERDIKT**. Never write them back to Linear/Jira. No new tokens, no external posts.
- Ticket-derived test cases are **read-only**. There is no local-override model.
- **No `confirm()` / `alert()` / `prompt()` anywhere.** Browser modals block the automation path this app depends on.
- Do not touch `.secrets.env`, `instance.config.json`, or `qa_targets.merge_added_test_cases`.
- The product is named **Verdikt** in user-facing copy.
- `agents` on a `Lane` is a `Record<AgentName, AgentStatus>`, **not an array**.
- Every task ends with a commit. Run the full check before each commit.

---

### Task 1: Backend — `update_case` in the store

**Files:**
- Modify: `backend/test_cases_store.py` (add function after `delete_case`, line ~84)
- Test: `backend/tests/test_test_cases_store.py` (append)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `test_cases_store.update_case(key: str, case_id: str, text: str, path: Optional[str] = None) -> Optional[dict]` — returns the updated case dict `{id, text, ts}`, or `None` when the text is blank or the id is unknown.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_test_cases_store.py`:

```python
def test_update_changes_text_and_preserves_id_ts_and_position(tmp_path):
    p = _p(tmp_path)
    a = tcs.add_case("NOR-8", "one", path=p)
    b = tcs.add_case("NOR-8", "two", path=p)
    tcs.add_case("NOR-8", "three", path=p)

    updated = tcs.update_case("NOR-8", b["id"], "  two edited  ", path=p)

    assert updated["text"] == "two edited"      # trimmed
    assert updated["id"] == b["id"]             # id preserved
    assert updated["ts"] == b["ts"]             # created-at preserved, not bumped
    # position preserved: the run scope is built in list order, so an edit
    # must not reorder the list
    assert [c["text"] for c in tcs.list_cases("NOR-8", path=p)] == [
        "one", "two edited", "three"
    ]
    assert a["id"] != b["id"]


def test_update_blank_text_is_rejected(tmp_path):
    p = _p(tmp_path)
    c = tcs.add_case("NOR-8", "keep me", path=p)
    assert tcs.update_case("NOR-8", c["id"], "   ", path=p) is None
    assert tcs.texts_for("NOR-8", path=p) == ["keep me"]  # unchanged on disk


def test_update_unknown_id_returns_none(tmp_path):
    p = _p(tmp_path)
    tcs.add_case("NOR-8", "keep me", path=p)
    assert tcs.update_case("NOR-8", "nope", "x", path=p) is None
    assert tcs.update_case("MISSING-1", "nope", "x", path=p) is None
    assert tcs.texts_for("NOR-8", path=p) == ["keep me"]


def test_update_result_is_readable_from_disk(tmp_path):
    p = _p(tmp_path)
    c = tcs.add_case("NOR-8", "before", path=p)
    tcs.update_case("NOR-8", c["id"], "after", path=p)
    assert tcs.texts_for("NOR-8", path=p) == ["after"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe -m pytest backend/tests/test_test_cases_store.py -v
```

Expected: the four new tests FAIL with `AttributeError: module 'test_cases_store' has no attribute 'update_case'`. The six pre-existing tests still pass.

- [ ] **Step 3: Write the implementation**

In `backend/test_cases_store.py`, insert between `delete_case` and `texts_for`:

```python
def update_case(key: str, case_id: str, text: str,
                path: Optional[str] = None) -> Optional[dict]:
    """Edit a case's text in place.

    Returns the updated case, or None when the text is blank or the id is not
    found (nothing is written in either case). The case's id, ts, and position
    in the list are preserved: the list is oldest-first and qa_targets builds
    the run scope in that order, so an edit must not reorder it.
    """
    text = (text or "").strip()
    if not text:
        return None
    path = path or STORE_PATH
    with _LOCK:
        data = _load(path)
        for case in data.get(key, []):
            if case.get("id") == case_id:
                case["text"] = text
                _save(path, data)
                return dict(case)
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe -m pytest backend/tests/test_test_cases_store.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/SCRIBE
git add backend/test_cases_store.py backend/tests/test_test_cases_store.py
git commit -m "feat(test-cases): update_case edits a stored case in place

Preserves id, ts, and list position — qa_targets builds the run scope in
list order, so an edit must not reorder it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backend — `PATCH /api/test-cases/{key}/{case_id}`

**Files:**
- Modify: `backend/server.py:285-287` (add route after `api_test_cases_delete`)
- Test: `backend/tests/test_test_cases_endpoints.py` (create)

**Interfaces:**
- Consumes: `test_cases_store.update_case(key, case_id, text, path=None)` from Task 1.
- Produces: `PATCH /api/test-cases/{key}/{case_id}` with body `{"text": "..."}` → `200 {"ok": true, "case": {id, text, ts}}`; `400 {"ok": false, "error": "text is required"}`; `404 {"ok": false, "error": "no such test case"}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_test_cases_endpoints.py`:

```python
"""Endpoint tests for the local test-case store routes."""
from fastapi.testclient import TestClient
import pytest

import server
import test_cases_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the store at a temp file so tests never touch the real
    # ~/qa-dashboard/test-cases.json. STORE_PATH is read at call time.
    monkeypatch.setattr(test_cases_store, "STORE_PATH", str(tmp_path / "test-cases.json"))
    return TestClient(server.app)


def _add(client, key="NOR-8", text="original"):
    res = client.post(f"/api/test-cases/{key}", json={"text": text})
    assert res.status_code == 200
    return res.json()["case"]


def test_patch_updates_text(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={"text": "edited"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["case"]["text"] == "edited"
    assert body["case"]["id"] == case["id"]
    # and the change is visible through the list route
    listed = client.get("/api/test-cases/NOR-8").json()["cases"]
    assert [c["text"] for c in listed] == ["edited"]


def test_patch_blank_text_is_400(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={"text": "   "})
    assert res.status_code == 400
    assert res.json()["ok"] is False
    assert client.get("/api/test-cases/NOR-8").json()["cases"][0]["text"] == "original"


def test_patch_unknown_id_is_404(client):
    _add(client)
    res = client.patch("/api/test-cases/NOR-8/does-not-exist", json={"text": "edited"})
    assert res.status_code == 404
    assert res.json()["ok"] is False


def test_patch_missing_body_key_is_400(client):
    case = _add(client)
    res = client.patch(f"/api/test-cases/NOR-8/{case['id']}", json={})
    assert res.status_code == 400
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe -m pytest backend/tests/test_test_cases_endpoints.py -v
```

Expected: all four FAIL with `405 Method Not Allowed` (the route does not exist yet).

- [ ] **Step 3: Write the implementation**

In `backend/server.py`, immediately after `api_test_cases_delete` (line ~287):

```python
@app.patch("/api/test-cases/{key}/{case_id}")
async def api_test_cases_update(key: str, case_id: str, payload: Dict[str, Any]):
    """Edit a local test case's text. Blank text -> 400; unknown id -> 404."""
    text = (payload or {}).get("text", "")
    if not (text or "").strip():
        return JSONResponse(status_code=400, content={"ok": False, "error": "text is required"})
    case = test_cases_store.update_case(key, case_id, text)
    if not case:
        return JSONResponse(status_code=404, content={"ok": False, "error": "no such test case"})
    return {"ok": True, "case": case}
```

Note the blank check happens in the route so blank text reports 400 rather than being indistinguishable from a missing id (both make `update_case` return `None`).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe -m pytest backend/tests/test_test_cases_endpoints.py backend/tests/test_test_cases_store.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/SCRIBE
git add backend/server.py backend/tests/test_test_cases_endpoints.py
git commit -m "feat(test-cases): PATCH route to edit a local test case

400 on blank text, 404 on unknown id. Cases stay local to Verdikt — nothing
is written back to the tracker.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — extract the ticket parser into a pure, tested module

**Files:**
- Create: `frontend/src/testCases.ts`
- Create: `frontend/tests/testCases.test.ts`
- Modify: `frontend/package.json:10` (the `test` script, so it runs both test files)
- Modify: `frontend/src/components/QueueRow.tsx` (delete the local `extractTestCases`, import the shared one)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `extractTicketTestCases(description: string): string[]`
  - `caseCount(ticketCount: number, addedCount: number): number`

- [ ] **Step 1: Read the existing parser you are moving**

Open `frontend/src/components/QueueRow.tsx` and read the whole `extractTestCases` function (starts at line ~386, runs to the end of the file). Move it **verbatim** — same regexes, same behavior. Renaming the function is the only change. Do not "improve" it in this task; the tests in Step 2 pin its current behavior first.

- [ ] **Step 2: Write the failing test**

Create `frontend/tests/testCases.test.ts`:

```typescript
// Framework-free tests for src/testCases.ts — run via `npm test` (esbuild → node).
import { extractTicketTestCases, caseCount } from '../src/testCases'

let passed = 0
let failed = 0

function eq<T>(actual: T, expected: T, label: string) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) {
    passed++
  } else {
    failed++
    console.error(`  FAIL ${label}\n    expected ${e}\n    got      ${a}`)
  }
}

// --- extractTicketTestCases -------------------------------------------------
{
  eq(extractTicketTestCases(''), [], 'empty description -> no cases')
  eq(extractTicketTestCases('Just a plain description with no section.'), [],
    'no Test Cases section -> no cases')

  const markdown = [
    '## Summary',
    'Fix the invoice total.',
    '',
    '## Test Cases',
    '- Open an invoice and confirm the total matches the line items',
    '- Change a line item and confirm the total recalculates',
    '',
    '## Notes',
    '- This note is NOT a test case',
  ].join('\n')
  eq(extractTicketTestCases(markdown), [
    'Open an invoice and confirm the total matches the line items',
    'Change a line item and confirm the total recalculates',
  ], 'markdown heading section, terminated by the next heading')

  const bold = [
    '**Test Cases:**',
    '- Log in as an admin',
    '- Log in as a viewer',
  ].join('\n')
  eq(extractTicketTestCases(bold), ['Log in as an admin', 'Log in as a viewer'],
    'bold-line heading variant')

  const trailing = ['## Test Cases', '- Only case here'].join('\n')
  eq(extractTicketTestCases(trailing), ['Only case here'],
    'section running to end of description')
}

// --- caseCount --------------------------------------------------------------
{
  eq(caseCount(0, 0), 0, 'no cases -> 0')
  eq(caseCount(2, 1), 3, 'ticket cases + added cases')
}

// --- report -----------------------------------------------------------------
console.log(`\ntestCases: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
```

**If a case in Step 4 fails because the moved parser genuinely behaves differently than the test asserts** (e.g. the bold-heading regex is stricter than expected), fix the *test* to match the real behavior and note it — this task pins existing behavior, it does not change it. Only the empty-description and `caseCount` assertions are non-negotiable.

- [ ] **Step 3: Wire the new test file into `npm test`**

In `frontend/package.json`, replace the `test` script (line 10) with:

```json
    "test": "esbuild tests/laneStatus.test.ts --bundle --platform=node --format=esm --outfile=node_modules/.cache/scribe-tests.mjs && node node_modules/.cache/scribe-tests.mjs && esbuild tests/testCases.test.ts --bundle --platform=node --format=esm --outfile=node_modules/.cache/scribe-tests-cases.mjs && node node_modules/.cache/scribe-tests-cases.mjs"
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd ~/SCRIBE/frontend && npm test
```

Expected: `laneStatus` passes, then the second esbuild step FAILS with `Could not resolve "../src/testCases"`.

- [ ] **Step 5: Create the module**

Create `frontend/src/testCases.ts` — the body of `extractTicketTestCases` is the existing `extractTestCases` from `QueueRow.tsx`, moved verbatim and renamed:

```typescript
/** Ticket-derived and user-added QA test cases.
 *
 *  Ticket cases are parsed out of the ticket description and are READ-ONLY —
 *  the tracker owns them. User-added cases live in the backend test-case store
 *  and are merged into the run scope by qa_targets.
 */

/** Pull the ticket's own test cases from a `Test Cases` section of the
 *  description (the checklist items under a "## Test Cases" heading).
 *  Empty when there's none. */
export function extractTicketTestCases(description: string): string[] {
  // <-- paste the body of extractTestCases from QueueRow.tsx here, unchanged
}

/** Badge count for the card button: ticket-derived cases plus the user's own. */
export function caseCount(ticketCount: number, addedCount: number): number {
  return ticketCount + addedCount
}
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd ~/SCRIBE/frontend && npm test
```

Expected: `laneStatus: N passed, 0 failed` then `testCases: 7 passed, 0 failed`.

- [ ] **Step 7: Point QueueRow at the shared parser**

In `frontend/src/components/QueueRow.tsx`:
1. Delete the whole local `extractTestCases` function at the bottom of the file.
2. Add to the imports at the top: `import { extractTicketTestCases } from '../testCases'`
3. Change line ~36 from `const ticketCases = extractTestCases(ticket.description)` to `const ticketCases = extractTicketTestCases(ticket.description)`

- [ ] **Step 8: Verify the app still builds**

```bash
cd ~/SCRIBE/frontend && npm run build
```

Expected: `tsc` clean, then `✓ built in ...`. No unused-import or missing-symbol errors.

- [ ] **Step 9: Commit**

```bash
cd ~/SCRIBE
git add frontend/src/testCases.ts frontend/tests/testCases.test.ts frontend/package.json frontend/src/components/QueueRow.tsx
git commit -m "refactor(test-cases): extract ticket-description parser into a pure module

extractTestCases was private to QueueRow, so it was untestable and unusable
from LaneCard. Moved verbatim to src/testCases.ts with tests pinning its
current behavior.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — `updateTestCase` API client

**Files:**
- Modify: `frontend/src/api.ts:123` (add after `deleteTestCase`)

**Interfaces:**
- Consumes: the `PATCH` route from Task 2; the existing `TestCase` interface at `api.ts:91`.
- Produces: `updateTestCase(key: string, id: string, text: string): Promise<UpdateTestCaseResult>` where
  `export interface UpdateTestCaseResult { ok: boolean; case?: TestCase; error?: string }`.

Unlike the existing `addTestCase` / `deleteTestCase` (which swallow failures into `null` / `false`), this one returns the error text so the modal can show it inline, per the spec's error-handling section.

- [ ] **Step 1: Add the function**

In `frontend/src/api.ts`, directly after `deleteTestCase` (line ~123):

```typescript
export interface UpdateTestCaseResult { ok: boolean; case?: TestCase; error?: string }

/** Edit an added case's text. Returns the error message so the caller can show it inline. */
export async function updateTestCase(key: string, id: string, text: string): Promise<UpdateTestCaseResult> {
  try {
    const res = await fetch(
      `${BASE}/test-cases/${encodeURIComponent(key)}/${encodeURIComponent(id)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      },
    )
    const data = await res.json().catch(() => null)
    if (data && data.ok && data.case) return { ok: true, case: data.case }
    return { ok: false, error: (data && data.error) || `status ${res.status}` }
  } catch {
    return { ok: false, error: 'could not reach the server' }
  }
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd ~/SCRIBE/frontend && npm run build
```

Expected: clean build.

- [ ] **Step 3: Commit**

```bash
cd ~/SCRIBE
git add frontend/src/api.ts
git commit -m "feat(test-cases): updateTestCase client that surfaces the error message

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — the `TestCasesModal` component

**Files:**
- Create: `frontend/src/components/TestCases/TestCasesModal.tsx`

**Interfaces:**
- Consumes: `Modal` from `../Modal`; `extractTicketTestCases` from `../../testCases`; `fetchTestCases`, `addTestCase`, `deleteTestCase`, `updateTestCase`, `TestCase` from `../../api`; `Ticket` from `../../types`.
- Produces: default-exported `TestCasesModal` with props
  ```typescript
  interface Props {
    ticket: Ticket
    runActive?: boolean
    onClose: () => void
    onCountChange?: (n: number) => void
  }
  ```

- [ ] **Step 1: Create the component**

Create `frontend/src/components/TestCases/TestCasesModal.tsx`:

```typescript
import { useState, useEffect, FormEvent } from 'react'
import Modal from '../Modal'
import { Ticket } from '../../types'
import { extractTicketTestCases } from '../../testCases'
import {
  fetchTestCases, addTestCase, deleteTestCase, updateTestCase, TestCase,
} from '../../api'

interface Props {
  ticket: Ticket
  /** A stage of this ticket's lane is currently working — added cases land in the NEXT run. */
  runActive?: boolean
  onClose: () => void
  /** Report the ticket+added total so the card badge stays in sync without refetching. */
  onCountChange?: (n: number) => void
}

const TAG: React.CSSProperties = { fontSize: 9, marginLeft: 6 }
const ROW: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
  borderBottom: '1px solid var(--border)',
}
const GROUP_TITLE: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, color: 'var(--text-dim)', margin: '14px 0 4px',
  textTransform: 'uppercase', letterSpacing: 0.4,
}

export default function TestCasesModal({ ticket, runActive = false, onClose, onCountChange }: Props) {
  const ticketCases = extractTicketTestCases(ticket.description)

  const [added, setAdded] = useState<TestCase[]>([])
  const [loadError, setLoadError] = useState('')
  const [newCase, setNewCase] = useState('')
  const [addError, setAddError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [editError, setEditError] = useState('')

  async function load() {
    setLoadError('')
    try {
      const cs = await fetchTestCases(ticket.key)
      setAdded(cs)
    } catch {
      setLoadError("Couldn't load your added cases.")
    }
  }

  useEffect(() => {
    let alive = true
    fetchTestCases(ticket.key)
      .then((cs) => { if (alive) setAdded(cs) })
      .catch(() => { if (alive) setLoadError("Couldn't load your added cases.") })
    return () => { alive = false }
  }, [ticket.key])

  // Keep the card's badge in step with what's in the modal.
  useEffect(() => {
    onCountChange?.(ticketCases.length + added.length)
  }, [ticketCases.length, added.length])

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    const text = newCase.trim()
    if (!text) return
    setBusy(true)
    setAddError('')
    const created = await addTestCase(ticket.key, text)
    setBusy(false)
    if (created) {
      setAdded((prev) => [...prev, created])
      setNewCase('')
    } else {
      setAddError("Couldn't save that case — it wasn't added.")
    }
  }

  async function handleDelete(id: string) {
    const ok = await deleteTestCase(ticket.key, id)
    if (ok) {
      setAdded((prev) => prev.filter((c) => c.id !== id))
    } else {
      // Already gone (or a write failed) — resync rather than leave a phantom row.
      load()
    }
  }

  function startEdit(c: TestCase) {
    setEditingId(c.id)
    setEditText(c.text)
    setEditError('')
  }

  async function saveEdit(id: string) {
    const text = editText.trim()
    if (!text) return
    setBusy(true)
    const res = await updateTestCase(ticket.key, id, text)
    setBusy(false)
    if (res.ok && res.case) {
      const saved = res.case
      setAdded((prev) => prev.map((c) => (c.id === id ? saved : c)))
      setEditingId(null)
      setEditError('')
    } else {
      // Stay in edit mode with the user's text intact.
      setEditError(res.error || 'could not save')
    }
  }

  const total = ticketCases.length + added.length

  return (
    <Modal title={`Test cases — ${ticket.key}`} onClose={onClose}>
      {runActive && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 10 }}>
          Run in progress — cases added now apply to the next run.
        </div>
      )}

      <div style={GROUP_TITLE}>From the ticket ({ticketCases.length})</div>
      {ticketCases.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          No test cases in the ticket description.
        </div>
      ) : (
        ticketCases.map((tc, i) => (
          <div key={`t${i}`} style={ROW}>
            <span style={{ fontSize: 12 }}>
              {tc}
              <span style={{ ...TAG, color: 'var(--text-dim)' }}>from ticket</span>
            </span>
          </div>
        ))
      )}

      <div style={GROUP_TITLE}>Added in Verdikt ({added.length})</div>
      {loadError && (
        <div style={{ fontSize: 11, color: 'var(--danger)' }}>
          {loadError}{' '}
          <button type="button" className="btn btn--ghost btn--small" onClick={load}>Retry</button>
        </div>
      )}
      {!loadError && added.length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          None yet — add one below.
        </div>
      )}
      {added.map((c) => (
        <div key={c.id} style={ROW}>
          {editingId === c.id ? (
            <>
              <input
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                style={{ flex: 1, fontSize: 12, padding: '4px 6px' }}
                autoFocus
              />
              <button
                type="button"
                className="btn btn--primary btn--small"
                disabled={busy || !editText.trim()}
                onClick={() => saveEdit(c.id)}
              >
                Save
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => { setEditingId(null); setEditError('') }}
              >
                Cancel
              </button>
              {editError && (
                <span style={{ fontSize: 10, color: 'var(--danger)' }}>{editError}</span>
              )}
            </>
          ) : (
            <>
              <span style={{ flex: 1, fontSize: 12 }}>
                {c.text}
                <span style={{ ...TAG, color: 'var(--accent, #5b8cff)' }}>added</span>
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => startEdit(c)}
              >
                Edit
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                title="Remove this test case"
                onClick={() => handleDelete(c.id)}
              >
                {'✕'}
              </button>
            </>
          )}
        </div>
      ))}

      <form onSubmit={handleAdd} style={{ display: 'flex', gap: 6, marginTop: 12 }}>
        <input
          value={newCase}
          onChange={(e) => setNewCase(e.target.value)}
          placeholder="Add a test case — it'll be tested on the next run"
          style={{ flex: 1, fontSize: 12, padding: '5px 7px' }}
        />
        <button type="submit" className="btn btn--primary btn--small" disabled={busy || !newCase.trim()}>
          Add
        </button>
      </form>
      {addError && (
        <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{addError}</div>
      )}
      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 10 }}>
        {total} case{total === 1 ? '' : 's'} in scope. Added cases stay in Verdikt — they are
        never written back to the tracker.
      </div>
    </Modal>
  )
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd ~/SCRIBE/frontend && npm run build
```

Expected: clean build. (The component is not mounted anywhere yet — that's Tasks 6 and 7.)

- [ ] **Step 3: Commit**

```bash
cd ~/SCRIBE
git add frontend/src/components/TestCases/TestCasesModal.tsx
git commit -m "feat(test-cases): shared TestCasesModal — ticket cases read-only, added cases editable

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — Queue row button, inline block removed

**Files:**
- Modify: `frontend/src/components/QueueRow.tsx` (imports, state at 26-64, action area near 165, expander at 293-344)

**Interfaces:**
- Consumes: `TestCasesModal` from Task 5; `extractTicketTestCases` / `caseCount` from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Strip the inline test-case machinery**

In `frontend/src/components/QueueRow.tsx`:
1. Delete the state and handlers at lines ~38-64: `added`, `newCase`, `busy`, the `useEffect` that fetches on expand, `handleAddCase`, `handleDeleteCase`.
2. Delete the test-case JSX inside the `expanded` block (lines ~310-342): the "Test cases" title, the `ticketCases`/`added` lists, and the add form. **Keep** the Acceptance Criteria block above it.
3. Update the imports on line 1 and 5:
   ```typescript
   import { useState, useEffect } from 'react'
   ```
   ```typescript
   import { fetchTestCases } from '../api'
   ```
   (`FormEvent`, `addTestCase`, `deleteTestCase`, and `TestCase` are no longer used here — `tsc` will fail the build if any stale import remains.)

- [ ] **Step 2: Add the button state and badge count**

Replace the deleted state block with:

```typescript
  const [showCases, setShowCases] = useState(false)
  const [addedCount, setAddedCount] = useState(0)

  // The badge needs the added-case count without opening the modal. This is the
  // same per-row fetch the inline block already did — see the spec's "Known cost".
  useEffect(() => {
    let alive = true
    fetchTestCases(ticket.key).then((cs) => { if (alive) setAddedCount(cs.length) })
    return () => { alive = false }
  }, [ticket.key])
```

and import the modal + count helper:

```typescript
import TestCasesModal from './TestCases/TestCasesModal'
import { extractTicketTestCases, caseCount } from '../testCases'
```

- [ ] **Step 3: Add the button to the row's action area**

In the row's action area, immediately **before** the `<div style={{ position: 'relative' }}>` that wraps the Start button (line ~165):

```tsx
        <button
          className="btn btn--ghost btn--small"
          title="View the ticket's test cases and add your own"
          onClick={(e) => { e.stopPropagation(); setShowCases(true) }}
        >
          Test cases ({caseCount(ticketCases.length, addedCount)})
        </button>
```

and render the modal just before the closing `</>` of the component's return:

```tsx
      {showCases && (
        <TestCasesModal
          ticket={ticket}
          onClose={() => setShowCases(false)}
          onCountChange={(n) => setAddedCount(Math.max(0, n - ticketCases.length))}
        />
      )}
```

`e.stopPropagation()` matters: the row key toggles `expanded` on click, and without it opening the modal would also expand the row.

- [ ] **Step 4: Verify the build**

```bash
cd ~/SCRIBE/frontend && npm run build && npm test
```

Expected: clean `tsc`, successful vite build, both test files pass. If `tsc` reports an unused import, you missed one in Step 1.

- [ ] **Step 5: Commit**

```bash
cd ~/SCRIBE
git add frontend/src/components/QueueRow.tsx
git commit -m "feat(test-cases): Test cases button on the queue row, inline block removed

The expander keeps Acceptance Criteria; test cases move to the shared modal so
there is one editing surface instead of two renderings to keep in sync.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — Active-lane card button

**Files:**
- Modify: `frontend/src/components/LaneCard.tsx` (imports at 1-7, state at ~50, header action area at 112-126)

**Interfaces:**
- Consumes: `TestCasesModal` from Task 5; `extractTicketTestCases` / `caseCount` from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add imports and state**

In `frontend/src/components/LaneCard.tsx`, add to the imports:

```typescript
import TestCasesModal from './TestCases/TestCasesModal'
import { extractTicketTestCases, caseCount } from '../testCases'
import { fetchTestCases } from '../api'
```

(`getTicketUsage` is already imported from `../api` on line 5 — merge `fetchTestCases` into that existing import rather than adding a second one.)

Add beside the existing `useState` calls (line ~50):

```typescript
  const [showCases, setShowCases] = useState(false)
  const [addedCount, setAddedCount] = useState(0)
```

and beside the existing usage `useEffect`:

```typescript
  useEffect(() => {
    let alive = true
    fetchTestCases(ticket.key).then((cs) => { if (alive) setAddedCount(cs.length) })
    return () => { alive = false }
  }, [ticket.key])
```

- [ ] **Step 2: Derive `runActive` and add the button**

Below `const { ticket, agents, currentAgent } = lane`, add:

```typescript
  // agents is a Record<AgentName, AgentStatus>, not an array.
  const runActive = Object.values(agents).some((a) => a?.state === 'active')
  const ticketCases = extractTicketTestCases(ticket.description)
```

In the header action `<div style={{ display: 'flex', gap: 6 }}>` (line ~112), **before** the `reportUrl` link:

```tsx
          <button
            className="btn btn--ghost btn--small"
            title="View the ticket's test cases and add your own"
            onClick={() => setShowCases(true)}
          >
            Test cases ({caseCount(ticketCases.length, addedCount)})
          </button>
```

Render the modal immediately before the component's final closing `</div>`:

```tsx
      {showCases && (
        <TestCasesModal
          ticket={ticket}
          runActive={runActive}
          onClose={() => setShowCases(false)}
          onCountChange={(n) => setAddedCount(Math.max(0, n - ticketCases.length))}
        />
      )}
```

- [ ] **Step 3: Verify the build**

```bash
cd ~/SCRIBE/frontend && npm run build && npm test
```

Expected: clean build, both test files pass.

- [ ] **Step 4: Full check before committing**

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe -m pytest backend/tests -q 2>&1 | tail -5
```

Expected: the suite's known-good state — **8 pre-existing Windows failures** (`.sh` claude-stub fixtures hitting `WinError 193`, and `test_model_defaults` asserting stale haiku ids). Any failure in `test_test_cases_*` or a 9th failure is yours to fix.

- [ ] **Step 5: Commit**

```bash
cd ~/SCRIBE
git add frontend/src/components/LaneCard.tsx
git commit -m "feat(test-cases): Test cases button on the active lane card

Shows the run-in-progress notice: cases added mid-run apply to the next run,
because qa_targets reads the store once at run start.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Live verification — the acceptance gate

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: a go/no-go. **This is what closes the feature. Automated tests do not.**

- [ ] **Step 1: Boot the app**

```bash
cd ~/SCRIBE && ./start.sh
```

Expected: uvicorn on `:8000`, vite on `:5173`. The frontend calls the backend through the Vite proxy at relative `/api` — do not hardcode a host.

- [ ] **Step 2: Queue-row checks**

On a real ticket in the queue:
1. The row shows a `Test cases (N)` button; clicking it opens the modal and does **not** expand the row.
2. Ticket-derived cases render under "From the ticket" with no Edit/✕ controls.
3. Add a case → it appears under "Added in Verdikt" with an `added` tag; the button's `(N)` increments.
4. Edit that case → Save → the new text persists; Cancel on another edit discards.
5. Delete a case → it disappears; the count drops.
6. Close and reopen the modal → everything persisted.
7. Expand the row's chevron → Acceptance Criteria still render, and the old inline test-case block is **gone**.

- [ ] **Step 3: Lane-card checks**

Start a run on that ticket, then from the Active-lane card:
1. The card shows the same `Test cases (N)` button.
2. The modal shows the identical list.
3. While a stage is working, the "Run in progress — cases added now apply to the next run." notice appears; it is absent once every stage is idle/done.

- [ ] **Step 4: End-to-end scope check**

Run QA on a ticket that has an added case, then confirm the case reached the run scope:

```bash
cd ~/SCRIBE && .venv/Scripts/python.exe backend/qa_targets.py <TICKET-KEY> <ENV-URL> --no-network
```

Expected: the printed JSON's scope text contains your added case. This proves the `test_cases_store` → `texts_for` → `merge_added_test_cases` path still works after the edit feature.

- [ ] **Step 5: Report the result**

If everything passes, say so plainly with the evidence. If anything fails, do **not** mark the feature done — fix it and re-run this task from Step 1.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `update_case` preserving id/ts/position | 1 |
| `PATCH` route, 400 blank / 404 unknown | 2 |
| `texts_for` / `merge_added_test_cases` unchanged | verified in 8 Step 4 |
| `testCases.ts` pure module + parser tests | 3 |
| `updateTestCase` API client | 4 |
| Shared modal, both tiers, run-active notice, no `confirm()` | 5 |
| Queue-row button; inline block removed | 6 |
| Lane-card button; `runActive` from the agents Record | 7 |
| Inline errors; retry on load failure; refetch on stale delete | 5 |
| Live verification as the acceptance gate | 8 |

**Known gap, deliberately accepted:** the `(N)` badge keeps the existing per-row `fetchTestCases` call (N+1 requests for an N-row queue). The spec marks the bulk `GET /api/test-cases` count endpoint as out of scope, and this is not a regression — the same fetch ships today.

**Type consistency:** `extractTicketTestCases` is named identically in Tasks 3, 5, 6, and 7; `caseCount` in Tasks 3, 6, and 7 (the modal sums the two lists it already holds). `UpdateTestCaseResult` is defined in Task 4 and consumed in Task 5. `update_case`'s signature in Task 1 matches its call site in Task 2. `agents` is treated as a Record in Task 7, matching `types.ts`.
