# Test Cases on the Card — Design Spec

**Date:** 2026-07-20
**Status:** Approved (interactive brainstorm; live verification is an explicit gate)
**Repo:** `~/SCRIBE` (oxagile-ca/SCRIBE) — React/Vite frontend + FastAPI backend

## Problem

A user-added test-case store already exists and already feeds real QA runs
(`backend/test_cases_store.py` → `/api/test-cases/*` → `qa_targets.py:453
texts_for()` → `merge_added_test_cases`). But its only UI is buried:

- It lives inside the Queue row's generic expand-chevron, below Acceptance
  Criteria, at 11px — not a surface you'd think to look for.
- There is **no way to edit** an added case; only add and delete.
- `LaneCard.tsx` — the Active-lane card, where a run is actually happening and
  where "what is being tested?" is the live question — has **no** test-case
  surface at all.
- The parser that pulls a ticket's own cases out of its description
  (`extractTestCases`) is private to `QueueRow.tsx` and therefore untestable and
  unusable from the lane card.

## Goal

A `Test cases (N)` button on **both** cards that opens one shared modal showing
the ticket's own test cases and letting the user add, edit, and delete their own
cases on top of them.

## Decisions (locked)

1. **Surfaces:** both the Queue row (`QueueRow.tsx`) and the Active-lane card
   (`LaneCard.tsx`), sharing one `TestCasesModal` so they cannot drift.
2. **Content:** planning only — cases parsed **from the ticket** (read-only) plus
   the user's **added** cases (editable). Last-run executed results are *not*
   shown; they stay in the evidence/report views.
3. **Permissions:** ticket-derived cases are strictly read-only; added cases get
   add / edit / delete. The tracker owns tier 1, VERDIKT owns tier 2. No local
   overrides of ticket cases — `merge_added_test_cases` has no notion of one.
4. **Replace, don't duplicate:** the Queue row's inline test-case block is
   removed (its expander keeps Acceptance Criteria only). The modal is the single
   editing surface; the `(N)` badge preserves at-a-glance count.
5. **Mid-run honesty:** when the ticket has an active lane, the modal states that
   cases added now apply to the *next* run. Editing stays enabled. No re-run
   action is added.
6. **Structure:** the modal is a self-contained leaf component owning its own
   state, mounted by each card. No hoisting to `App.tsx`, no prop-drilling
   through `Queue`/`ActiveLanes`.

## Backend design

### `backend/test_cases_store.py`
New `update_case(key, case_id, text, path=None) -> Optional[dict]`:
- Trims `text`; returns `None` when blank (nothing written) or when `case_id` is
  not found.
- **Preserves the case's original `id`, `ts`, and list position.** The list is
  oldest-first and the run scope is built in that order — an edit must not
  reorder it.
- Uses the same `_LOCK` + atomic `_save` (tmp file + `os.replace`) as the
  existing functions.

### `backend/server.py`
New route beside the existing three:

| Method / Route | Behavior |
|---|---|
| `PATCH /api/test-cases/{key}/{case_id}` | Body `{"text": "..."}`. `{ok: true, case}` on success; `{ok: false, error}` with **400** for blank text, **404** for an unknown id. |

### Unchanged
`texts_for()` and `qa_targets.merge_added_test_cases` need no edits — they read
whatever text is current when a run starts, so edits propagate for free. Added
cases remain local to VERDIKT: nothing is written back to Linear/Jira, and no
write token is required. `.secrets.env` and `instance.config.json` are untouched.

**Note for the npm-package work:** the store path is `~/qa-dashboard/test-cases.json`,
overridable via `SCRIBE_TEST_CASES`. That env var must be redirected alongside the
other config paths when the app is packaged.

## Frontend design

### New files
- **`frontend/src/testCases.ts`** — pure, no React:
  - `extractTicketTestCases(description: string): string[]` — moved verbatim out
    of `QueueRow.tsx`.
  - `caseCount(ticketCases, added): number`.
- **`frontend/src/components/TestCases/TestCasesModal.tsx`** — the feature.
  Props: `ticket`, `runActive?: boolean`, `onClose`, `onCountChange?` (so the
  card's badge updates without a refetch). Owns fetch / add / edit / delete /
  busy / error state.

### Modal layout
Built on the existing `Modal.tsx` (Esc-to-close and overlay-click-to-close are
already handled there).

- Title: `Test cases — <TICKET-KEY>`.
- When `runActive`: one muted notice — *"Run in progress — cases added now apply
  to the next run."*
- **From the ticket (N)** — read-only rows with a `from ticket` tag. Empty state:
  *"No test cases in the ticket description."*
- **Added in VERDIKT (N)** — each row has Edit and ✕. Edit flips that single row
  into an inline input with Save/Cancel (Save → `PATCH`). Delete is immediate,
  matching today's behavior — **no `confirm()` dialog**, which would block the
  browser-automation path this app depends on.
- Add form pinned at the bottom: text input + Add.

### Edited files
- **`frontend/src/api.ts`** — add `updateTestCase(key, id, text)` beside the
  existing `fetchTestCases` / `addTestCase` / `deleteTestCase`.
- **`QueueRow.tsx`** — remove `extractTestCases`, the test-case JSX, and the
  `newCase` / `busy` / add / delete handlers; the expander keeps Acceptance
  Criteria only. Add a `Test cases (N)` button and one `showCases` boolean. Net
  effect: the file (currently 408 lines, the largest card file) gets shorter.
- **`LaneCard.tsx`** — add the same button + boolean, passing
  `runActive = Object.values(agents).some(a => a?.state === 'active')` (`agents` is
  a `Record<AgentName, AgentStatus>`, not an array). The Queue row passes
  `runActive={false}`: queued tickets have no lane.

### Known cost, accepted
The `(N)` badge needs the added-case count per ticket, so each queue row keeps
its existing on-mount `fetchTestCases` call — an N+1 request pattern for an
N-row queue. **This is exactly what ships today, so it is not a regression.** If
the queue becomes slow, the follow-up is a bulk `GET /api/test-cases` returning
`{key: count}` and dropping the per-row fetch. Out of scope here.

## Error handling

- `POST`/`PATCH` failure → the row or add-form shows the error inline and stays
  in edit mode with the user's text intact. A failed save never loses input.
- Blank/whitespace text is rejected client-side (Add/Save disabled) before the
  request; the store rejects it too.
- `GET /api/test-cases/{key}` failure → the modal still renders the
  ticket-derived cases (they come from the ticket already in memory) plus an
  inline *"Couldn't load your added cases"* with a Retry.
- Deleting an already-deleted id returns `ok:false` → the modal refetches rather
  than leaving a phantom row.

## Testing

**Backend (pytest, extend `backend/tests/test_test_cases_store.py`):**
- `update_case` changes text while preserving `id`, `ts`, and list position.
- `update_case` returns `None` for blank text and for an unknown id.
- A write leaves valid JSON on disk (atomic-save behavior holds).
- Endpoint test: `PATCH` returns 200 / 400 (blank) / 404 (unknown id).

**Frontend (esbuild+node, new `frontend/tests/testCases.test.ts`):**
- `extractTicketTestCases` against real description shapes: a `## Test Cases`
  section, a `**Test Cases:**` bold-line variant, a section terminated by the
  next heading, and a description containing none. This parser has real edge
  cases and currently has **zero** tests; extracting it is what makes testing it
  possible.
- `caseCount` arithmetic.

**Live verification — the acceptance gate.** Automated tests do not close this
work. Boot backend + frontend, then on a real ticket:
1. Open the modal from the Queue row; confirm ticket-derived cases render.
2. Add a case; edit it; delete another; reopen and confirm persistence.
3. Open the same ticket's Active-lane card; confirm the identical list plus the
   run-in-progress notice.
4. Run QA and confirm an added case actually reaches the run scope via
   `qa_targets`.

## Addendum — "Re-test" on QAed queue rows (2026-07-20)

Decided in the same session, implemented in the same file (`QueueRow.tsx`), so it
is recorded here rather than in a separate spec.

**Problem.** A ticket that has already been QA'd is indistinguishable in the queue
from one that never has: `NOR-11` (0/100) and `NOR-12` (82/100) both offer the same
plain **Start**, which runs the *full* pipeline behind the env picker — provision,
build, deploy, test — when re-testing usually only needs the QA stage. The lane card
already relabels itself to "Retry QA" (`LaneCard.tsx:231`); the queue never got the
same treatment.

**Decisions (locked)**

1. A row where `isTicketQAed(ticket)` is true labels its button **"Re-test"** instead
   of "Start". Non-QAed rows are untouched.
2. **Env resolution splits on `needsBuildDeploy`** (the existing `App.tsx:63` flag):
   - **already-deployed app** (`needsBuildDeploy === false`) → one click, no picker.
     The request sends `envUrl: ""` and the backend resolves it via
     `qa_orchestrator.resolve_env_url` → `environments.staticUrls[0]`.
   - **build/deploy app** (`needsBuildDeploy === true`) → opens the **existing** env
     picker, then runs QA-only against the chosen env.
   Rejected: always-skip-the-picker (an app with no `staticUrls` would resolve to `""`
   and silently test nothing — the exact silent-wrong failure this codebase keeps
   fixing), and always-show-the-picker (pointless click when there is one answer).
3. **Re-test creates a lane, then runs QA.** Firing a background run with no lane card
   would leave the user with no progress, no logs, and no blocker banner. Re-test
   therefore does what Start does to establish the lane, then immediately triggers the
   QA run instead of waiting for a second click on "Run QA".
4. The single-run lock is respected: the backend returns **409** when another QA run is
   active (`server.py:1009`). Re-test surfaces that message as a toast and leaves the
   lane in place rather than failing silently.

**Pure logic (tested):** `queueActionLabel(isQAed)` and `retestNeedsEnvPicker(needsBuildDeploy)`
live in `src/testCases.ts`'s sibling module `src/queueActions.ts` so the branch in
decision 2 is unit-tested rather than buried in JSX.

## Out of scope

- Last-run executed results in the modal (decision 2).
- Editing ticket-derived cases or local overrides of them (decision 3).
- A "Re-run QA with these cases" action on a live lane (decision 5).
- The bulk count endpoint.
- Any write-back of test cases to Linear/Jira.
