# Cluster B — Ticket UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hero feature-breakdown card (#6), Difficulty + Oldest-first sort orderings (#7), and Group-by Epic/Label (#8) to the SCRIBE ticket queue.

**Architecture:** Fetch the real structural fields (parent/labels/createdAt/numeric priority) from Linear; compute a derived `difficulty` server-side in the existing `/api/tickets` enrichment loop; do grouping, new sorts, and the hero card client-side as pure transforms over the tickets array. No new endpoints, no new deps.

**Tech Stack:** Python (FastAPI, pytest), React 18 + TypeScript + Vite (plain CSS), httpx (already present).

## Global Constraints

- **Python interpreter / tests:** from `C:\Users\ankit\SCRIBE\backend` run `..\.venv\Scripts\python.exe -m pytest <file> -v`. Never the bare `python3`.
- **Frontend has no test runner:** verify with `npm run build` (tsc) from `C:\Users\ankit\SCRIBE\frontend`; manual check otherwise.
- **No new pip/npm deps.** parent/labels/createdAt/priority are GraphQL+dict changes; difficulty is pure Python; grouping/hero are pure TS.
- **Difficulty is a derived heuristic** computed server-side (story points are unused on the board): AC-count + description-length → `Easy`/`Medium`/`Hard` + numeric `difficultyScore`.
- **Group-by:** `None | Epic | Label`; missing key → "Ungrouped" (rendered last); multi-label tickets group under their **first** label.
- **Hero card:** feature = Epic/Parent; top 3 by ticket count, "show 2 more" → 5; row = title + count + QA-coverage bar; epic-less tickets roll up under their first label.
- **#7 are sort orderings** added to the existing Sort dropdown (Difficulty = Easy→Hard via `difficultyScore`; Created = Oldest first via `createdAt`).
- **Fetch, don't parse** title prefixes. Linear is the active tracker; Jira mapper left unchanged.
- **Branch:** `feat/scribe-demo-clusters` (already contains C + A; B lands here → unified A+B+C). Commit after every task.

---

## File structure

**New backend:** `backend/ticket_difficulty.py`. **Modified backend:** `backend/linear_client.py` (query + `_map_issue`), `backend/server.py` (`/api/tickets` enrichment), `backend/tests/test_linear_client.py` (extend), new `backend/tests/test_ticket_difficulty.py`, new `backend/tests/test_tickets_difficulty.py`.
**New frontend:** `frontend/src/ticketGroups.ts`, `frontend/src/components/FeatureBreakdown.tsx`. **Modified frontend:** `frontend/src/types.ts`, `frontend/src/components/Queue.tsx`, `frontend/src/App.tsx`, `frontend/src/styles/layout.css`.

---

## Task 1: `ticket_difficulty.py` — derived difficulty heuristic

**Files:**
- Create: `backend/ticket_difficulty.py`
- Test: `backend/tests/test_ticket_difficulty.py`

**Interfaces:**
- Produces: `count_acceptance_criteria(description: str) -> int`; `compute_difficulty(description: str) -> tuple[str, int]` (returns `(label, score)`, label in `Easy|Medium|Hard`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ticket_difficulty.py`:
```python
import ticket_difficulty as td


def test_empty_description_is_easy():
    assert td.compute_difficulty("") == ("Easy", 0)
    assert td.compute_difficulty(None) == ("Easy", 0)


def test_two_acs_is_easy():
    desc = "Acceptance Criteria\n- the first criterion line\n- the second criterion line"
    assert td.count_acceptance_criteria(desc) == 2
    assert td.compute_difficulty(desc) == ("Easy", 2)


def test_four_acs_is_medium():
    desc = "AC:\n- criterion alpha line\n- criterion beta line\n- criterion gamma line\n- criterion delta line"
    assert td.count_acceptance_criteria(desc) == 4
    assert td.compute_difficulty(desc)[0] == "Medium"


def test_six_acs_is_hard():
    desc = "Acceptance Criteria\n" + "\n".join(f"- criterion number {i} here" for i in range(6))
    assert td.count_acceptance_criteria(desc) == 6
    assert td.compute_difficulty(desc)[0] == "Hard"


def test_long_description_bumps_score_over_bucket():
    # 5 ACs (Medium) + a long body (>1200 chars) bumps +1 → 6 → Hard
    body = "x" * 1300
    desc = "Acceptance Criteria\n" + "\n".join(f"- criterion number {i} here" for i in range(5)) + "\n" + body
    label, score = td.compute_difficulty(desc)
    assert score >= 6 and label == "Hard"


def test_short_bullets_under_threshold_not_counted():
    # bullets <= 10 chars after de-bulleting are ignored (mirrors frontend extractACs)
    desc = "- short\n- ok"
    assert td.count_acceptance_criteria(desc) == 0
    assert td.compute_difficulty(desc) == ("Easy", 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_ticket_difficulty.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ticket_difficulty'`.

- [ ] **Step 3: Write the implementation**

Create `backend/ticket_difficulty.py`:
```python
"""Derived ticket difficulty (story points are unused on the board).

Mirrors the frontend `extractACs` heuristic: counts acceptance-criteria-style lines
(under an 'AC:'/'Acceptance Criteria' header, or bullet lines) and adds a small bump
for long descriptions, then buckets into Easy / Medium / Hard. Pure — no I/O.
"""
import re

_AC_PREFIX = re.compile(r"^ac[:\s]", re.I)
_AC_HEADER = re.compile(r"acceptance\s*criteria", re.I)
_BULLET = re.compile(r"^[\*\-]\s")
_BULLET_STRIP = re.compile(r"^[\*\-]\s*")


def count_acceptance_criteria(description: str) -> int:
    """Count AC-style lines. A header line ('AC:' / 'Acceptance Criteria') turns the AC
    section on (and is skipped); while in-section, or for any bullet line, the de-bulleted
    text counts when it is longer than 10 chars; a blank line ends the section."""
    if not description:
        return 0
    count = 0
    in_ac = False
    for raw in description.split("\n"):
        line = raw.strip()
        if _AC_PREFIX.match(line) or _AC_HEADER.search(line):
            in_ac = True
            continue
        if in_ac or _BULLET.match(line):
            clean = _BULLET_STRIP.sub("", line).strip()
            if len(clean) > 10:
                count += 1
        if in_ac and line == "":
            in_ac = False
    return count


def compute_difficulty(description: str) -> tuple[str, int]:
    """Return (label, score). score = AC count + length bump (+1 per 600 chars beyond the
    first 600, capped at +3). Buckets: <=2 Easy, 3..5 Medium, >=6 Hard."""
    description = description or ""
    ac = count_acceptance_criteria(description)
    length_bump = min(3, max(0, (len(description) - 600) // 600))
    score = ac + length_bump
    if score <= 2:
        label = "Easy"
    elif score <= 5:
        label = "Medium"
    else:
        label = "Hard"
    return label, score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_ticket_difficulty.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ticket_difficulty.py backend/tests/test_ticket_difficulty.py
git commit -m "feat(#7): ticket_difficulty heuristic (AC-count + length buckets)"
```

---

## Task 2: Linear field extension (parent / labels / createdAt / numeric priority)

**Files:**
- Modify: `backend/linear_client.py` (`_ISSUES_QUERY` `:19-34`, `_map_issue` `:52-67`)
- Test: `backend/tests/test_linear_client.py` (extend `SAMPLE` + assertions)

**Interfaces:**
- Produces: each mapped ticket now also has `createdAt: str`, `parent: {key,title}|None`, `labels: list[str]`, `priorityValue: int|None`.

- [ ] **Step 1: Write the failing test additions**

In `backend/tests/test_linear_client.py`, add a new test (and, if the module `SAMPLE` lacks these fields, add a node carrying them — do not break existing tests):
```python
def test_map_issue_includes_structural_fields():
    node = {
        "identifier": "INV-660", "title": "Get Folio", "description": "d",
        "priority": 2, "priorityLabel": "High", "createdAt": "2026-06-01T00:00:00.000Z",
        "state": {"name": "Ready for QA", "type": "started"},
        "assignee": {"displayName": "Ada Lovelace", "name": "ada"},
        "parent": {"identifier": "INV-654", "title": "Add Location Email field"},
        "labels": {"nodes": [{"name": "Back-End"}, {"name": "Bug"}]},
    }
    t = tickets_from_response({"data": {"issues": {"nodes": [node]}}})[0]
    assert t["createdAt"] == "2026-06-01T00:00:00.000Z"
    assert t["parent"] == {"key": "INV-654", "title": "Add Location Email field"}
    assert t["labels"] == ["Back-End", "Bug"]
    assert t["priorityValue"] == 2


def test_map_issue_defaults_structural_fields_when_absent():
    node = {"identifier": "INV-1", "title": "t", "state": {"name": "x", "type": "started"}}
    t = tickets_from_response({"data": {"issues": {"nodes": [node]}}})[0]
    assert t["createdAt"] == ""
    assert t["parent"] is None
    assert t["labels"] == []
    assert t["priorityValue"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_linear_client.py -v`
Expected: FAIL — `KeyError`/`AssertionError` on `createdAt`/`parent`/`labels`/`priorityValue` (not yet mapped).

- [ ] **Step 3: Extend the GraphQL query**

In `backend/linear_client.py`, replace `_ISSUES_QUERY` (`:19-34`) with:
```python
_ISSUES_QUERY = """
query Issues($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 100, after: $after, orderBy: updatedAt) {
    nodes {
      identifier
      title
      description
      priority
      priorityLabel
      createdAt
      updatedAt
      state { name type }
      assignee { displayName name }
      parent { identifier title }
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()
```

- [ ] **Step 4: Extend `_map_issue`**

In `backend/linear_client.py`, replace `_map_issue` (`:52-67`) with:
```python
def _map_issue(node: dict) -> dict:
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    parent = node.get("parent") or None
    label_nodes = ((node.get("labels") or {}).get("nodes")) or []
    return {
        "key": node.get("identifier", ""),
        "summary": node.get("title", ""),
        "status": state.get("name", ""),
        "priority": node.get("priorityLabel") or "Medium",
        "priorityValue": node.get("priority"),
        "assignee": _short(assignee.get("displayName") or assignee.get("name")),
        "qaAssignee": "",
        "description": node.get("description") or "",
        "flagged": False,
        "staleDays": 0,
        "createdAt": node.get("createdAt") or "",
        "parent": {"key": parent.get("identifier", ""), "title": parent.get("title", "")} if parent else None,
        "labels": [n.get("name", "") for n in label_nodes if n.get("name")],
        "devInfo": [],
        "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_linear_client.py -v`
Expected: all pass (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add backend/linear_client.py backend/tests/test_linear_client.py
git commit -m "feat(#8): fetch parent/labels/createdAt/numeric-priority from Linear"
```

---

## Task 3: `/api/tickets` difficulty enrichment

**Files:**
- Modify: `backend/server.py` (import + enrichment loop `:375-377`)
- Test: `backend/tests/test_tickets_difficulty.py`

**Interfaces:**
- Consumes: `ticket_difficulty.compute_difficulty`.
- Produces (HTTP): each ticket in `GET /api/tickets` carries `difficulty: str` + `difficultyScore: int`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_difficulty.py`:
```python
from fastapi.testclient import TestClient
import server
import linear_client


def test_tickets_carry_difficulty(monkeypatch):
    async def fake_get_tickets(token, projects):
        return [{
            "key": "INV-1", "summary": "x", "status": "Ready for QA",
            "priority": "Medium", "assignee": "", "qaAssignee": "",
            "description": "AC:\n- criterion alpha line\n- criterion beta line\n- criterion gamma line\n- criterion delta line",
            "flagged": False, "staleDays": 0, "devInfo": [],
            "evidence": {"status": "none", "score": None, "time": "", "reportPath": ""},
        }]
    monkeypatch.setattr(linear_client, "get_tickets", fake_get_tickets)
    monkeypatch.setattr(server, "check_evidence",
                        lambda k: {"status": "none", "score": None, "time": "", "reportPath": ""})
    client = TestClient(server.app)
    res = client.get("/api/tickets")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list) and body, f"expected non-empty list, got {body!r}"
    assert "difficulty" in body[0] and "difficultyScore" in body[0]
    assert body[0]["difficulty"] == "Medium"   # 4 ACs
```
(Assumes the live `instance.config.json` has `issueTracker.type == "linear"`, which routes `/api/tickets` through the monkeypatched `linear_client.get_tickets`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_tickets_difficulty.py -v`
Expected: FAIL — `assert "difficulty" in body[0]` (not yet computed).

- [ ] **Step 3: Add the import + enrichment**

In `backend/server.py`, add near the other local imports (e.g. after `import config_io`):
```python
import ticket_difficulty
```
In the `/api/tickets` enrichment loop (`:375-377`), add the difficulty lines so the loop reads:
```python
    for t in tickets:
        t["statusCategory"] = categorize_status(t.get("status", ""), mapping)
        t["evidence"] = check_evidence(t["key"])
        label, dscore = ticket_difficulty.compute_difficulty(t.get("description", ""))
        t["difficulty"] = label
        t["difficultyScore"] = dscore
    return tickets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_tickets_difficulty.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py backend/tests/test_tickets_difficulty.py
git commit -m "feat(#7): enrich /api/tickets with derived difficulty"
```

---

## Task 4: `types.ts` — extend the `Ticket` interface

**Files:**
- Modify: `frontend/src/types.ts` (`Ticket` `:12-25`)

**Interfaces:**
- Produces: `Ticket` gains `createdAt?: string`, `parent?: { key: string; title: string } | null`, `labels?: string[]`, `priorityValue?: number`, `difficulty?: 'Easy' | 'Medium' | 'Hard'`, `difficultyScore?: number`.

- [ ] **Step 1: Add the fields**

In `frontend/src/types.ts`, replace the `Ticket` interface (`:12-25`) with:
```ts
export interface Ticket {
  key: string
  summary: string
  status: string
  statusCategory?: 'ready_for_qa' | 'in_qa' | 'other'
  priority: string
  priorityValue?: number
  assignee: string
  qaAssignee: string
  description: string
  flagged: boolean
  staleDays: number
  createdAt?: string
  parent?: { key: string; title: string } | null
  labels?: string[]
  difficulty?: 'Easy' | 'Medium' | 'Hard'
  difficultyScore?: number
  devInfo: DevInfo[]
  evidence: EvidenceStatus
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds (purely additive optional fields).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(cluster-b): extend Ticket type (parent/labels/createdAt/difficulty)"
```

---

## Task 5: `ticketGroups.ts` — pure grouping + feature helpers

**Files:**
- Create: `frontend/src/ticketGroups.ts`

**Interfaces:**
- Consumes: `Ticket` (types.ts), `isTicketQAed` (QueueRow.tsx, already exported).
- Produces: `TicketGroup`, `FeatureSummary` interfaces; `groupTickets(tickets, by: 'epic'|'label') -> TicketGroup[]`; `topFeatures(tickets, n) -> FeatureSummary[]`.

- [ ] **Step 1: Create the module**

Create `frontend/src/ticketGroups.ts`:
```ts
import { Ticket } from './types'
import { isTicketQAed } from './components/QueueRow'

export interface TicketGroup { key: string; title: string; tickets: Ticket[] }
export interface FeatureSummary { key: string; title: string; total: number; qaed: number }

const UNGROUPED = '__ungrouped__'

/** Group tickets by epic (parent) or by first label. Tickets missing the key collect
 *  into a trailing 'Ungrouped' group. Real groups are ordered by size (desc). */
export function groupTickets(tickets: Ticket[], by: 'epic' | 'label'): TicketGroup[] {
  const groups = new Map<string, TicketGroup>()
  const ungrouped: Ticket[] = []
  for (const t of tickets) {
    let key: string | null = null
    let title = ''
    if (by === 'epic') {
      if (t.parent) { key = t.parent.key; title = t.parent.title || t.parent.key }
    } else {
      const first = (t.labels && t.labels[0]) || ''
      if (first) { key = first; title = first }
    }
    if (!key) { ungrouped.push(t); continue }
    let g = groups.get(key)
    if (!g) { g = { key, title, tickets: [] }; groups.set(key, g) }
    g.tickets.push(t)
  }
  const ordered = Array.from(groups.values()).sort((a, b) => b.tickets.length - a.tickets.length)
  if (ungrouped.length) ordered.push({ key: UNGROUPED, title: 'Ungrouped', tickets: ungrouped })
  return ordered
}

/** Top-N "features" by ticket count for the hero card. A feature is an epic (parent);
 *  epic-less tickets roll up under their first label so the card is never empty. */
export function topFeatures(tickets: Ticket[], n: number): FeatureSummary[] {
  const map = new Map<string, FeatureSummary>()
  for (const t of tickets) {
    let key: string
    let title: string
    if (t.parent) { key = `epic:${t.parent.key}`; title = t.parent.title || t.parent.key }
    else { const lbl = (t.labels && t.labels[0]) || 'Other'; key = `label:${lbl}`; title = lbl }
    let f = map.get(key)
    if (!f) { f = { key, title, total: 0, qaed: 0 }; map.set(key, f) }
    f.total += 1
    if (isTicketQAed(t)) f.qaed += 1
  }
  return Array.from(map.values()).sort((a, b) => b.total - a.total).slice(0, n)
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds (the import of `isTicketQAed` from `./components/QueueRow` resolves; no consumer yet, but tsc compiles the module).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/ticketGroups.ts
git commit -m "feat(cluster-b): ticketGroups — groupTickets + topFeatures pure helpers"
```

---

## Task 6: `Queue.tsx` — Difficulty/Created sorts + Group-by (#7 + #8)

**Files:**
- Modify: `frontend/src/components/Queue.tsx`

**Interfaces:**
- Consumes: `groupTickets` (ticketGroups.ts), the new `Ticket` fields.

- [ ] **Step 1: Extend `SortKey`, `SORTS`, `DEFAULT_DIR`**

In `frontend/src/components/Queue.tsx`:
- Replace `SortKey` (`:6`) with:
```tsx
type SortKey = 'priority' | 'difficulty' | 'created' | 'stale' | 'score' | 'key' | 'summary'
```
- Replace `SORTS` (`:34-40`) with:
```tsx
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'priority', label: 'Priority' },
  { key: 'difficulty', label: 'Difficulty (Easy→Hard)' },
  { key: 'created', label: 'Oldest first' },
  { key: 'stale', label: 'Stale days' },
  { key: 'score', label: 'QA score' },
  { key: 'key', label: 'Ticket key' },
  { key: 'summary', label: 'Summary' },
]
```
- Replace `DEFAULT_DIR` (`:43-45`) with:
```tsx
// Sensible default direction per sort field (e.g. most-stale / highest-score first).
const DEFAULT_DIR: Record<SortKey, SortDir> = {
  priority: 'asc', difficulty: 'asc', created: 'asc', stale: 'desc', score: 'desc', key: 'asc', summary: 'asc',
}
```

- [ ] **Step 2: Extend the sort switch**

Replace the `.sort()` block (`:68-89`) with (adds `difficulty`/`created` cases and uses numeric `priorityValue` when present):
```tsx
    .sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      switch (sortKey) {
        case 'difficulty':
          return ((a.difficultyScore ?? 0) - (b.difficultyScore ?? 0)) * dir
        case 'created': {
          const ca = a.createdAt ? Date.parse(a.createdAt) : Infinity
          const cb = b.createdAt ? Date.parse(b.createdAt) : Infinity
          return (ca - cb) * dir
        }
        case 'stale':
          return (a.staleDays - b.staleDays) * dir
        case 'score': {
          const sa = a.evidence?.score ?? -1
          const sb = b.evidence?.score ?? -1
          return (sa - sb) * dir
        }
        case 'key':
          return a.key.localeCompare(b.key, undefined, { numeric: true }) * dir
        case 'summary':
          return a.summary.localeCompare(b.summary) * dir
        case 'priority':
        default: {
          // Prefer Linear's numeric priority when present (None/0 sorts last); else the
          // label order. Ties fall back to most-stale first.
          const pv = (t: Ticket) => (t.priorityValue == null || t.priorityValue === 0) ? 99 : t.priorityValue
          const usePv = a.priorityValue != null || b.priorityValue != null
          const priDiff = usePv
            ? (pv(a) - pv(b))
            : ((PRI_ORDER[a.priority] ?? 2) - (PRI_ORDER[b.priority] ?? 2))
          const base = priDiff !== 0 ? priDiff : b.staleDays - a.staleDays
          return base * dir
        }
      }
    })
```

- [ ] **Step 3: Add Group-by state + control**

Add the group-by state next to the other `useState` hooks (near `:50-53`, after `sortDir`):
```tsx
  const [groupBy, setGroupBy] = useState<'none' | 'epic' | 'label'>('none')
```
Add the import at the top of the file (after the existing imports):
```tsx
import { groupTickets } from '../ticketGroups'
```
Add a Group-by `<select>` inside `.queue__controls`, right after the `.queue__sort` block (before its closing `</div>` of `.queue__controls`):
```tsx
        <div className="queue__group-by">
          <label className="queue__sort-label">Group</label>
          <select
            className="queue__sort-select"
            value={groupBy}
            onChange={e => setGroupBy(e.target.value as 'none' | 'epic' | 'label')}
          >
            <option value="none">None</option>
            <option value="epic">Epic</option>
            <option value="label">Label</option>
          </select>
        </div>
```

- [ ] **Step 4: Render grouped sections when grouping is on**

Replace the list-render block (`:153-164`, the `queueTickets.map(t => <QueueRow .../>)`) with a `renderRow` helper used by both flat and grouped modes:
```tsx
          (() => {
            const renderRow = (t: Ticket) => (
              <QueueRow
                key={t.key}
                ticket={t}
                onStart={onStart}
                disabled={lanesAreFull}
                environments={environments}
                envLocks={envLocks}
                pipelineState={pipelineByTicket?.[t.key]}
                onRetryProvision={onRetryProvision}
              />
            )
            if (groupBy === 'none') return queueTickets.map(renderRow)
            return groupTickets(queueTickets, groupBy).map(g => (
              <div key={g.key} className="queue__group">
                <div className="queue__group-header">
                  {g.title} <span className="queue__group-count">({g.tickets.length})</span>
                </div>
                {g.tickets.map(renderRow)}
              </div>
            ))
          })()
```
(The surrounding empty-state ternary at `:148-152` — `queueTickets.length === 0 ? (...) : (...)` — stays; this replaces only the `: (...)` branch's content.)

- [ ] **Step 5: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds. Manual (after backend restart): Sort dropdown shows "Difficulty (Easy→Hard)" and "Oldest first" and reorders the queue; the Group control shows None/Epic/Label — Epic groups the Location-Email epic's children together with an "Ungrouped" section last, Label groups Back-End/Front-End/…

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Queue.tsx
git commit -m "feat(#7,#8): Difficulty/Oldest sorts + Group-by epic/label in Queue"
```

---

## Task 7: `FeatureBreakdown.tsx` hero card (#6) + App wiring + CSS

**Files:**
- Create: `frontend/src/components/FeatureBreakdown.tsx`
- Modify: `frontend/src/App.tsx` (import + render between ActiveLanes and Queue), `frontend/src/styles/layout.css` (new classes)

**Interfaces:**
- Consumes: `topFeatures` (ticketGroups.ts), `Ticket`.

- [ ] **Step 1: Create the hero card**

Create `frontend/src/components/FeatureBreakdown.tsx`:
```tsx
import { useState } from 'react'
import { Ticket } from '../types'
import { topFeatures } from '../ticketGroups'

/** HERO breakdown of the features (epics) being worked on: top 3 by ticket count,
 *  "Show N more" expands to 5; each row shows a QA-coverage bar. */
export default function FeatureBreakdown({ tickets }: { tickets: Ticket[] }) {
  const [expanded, setExpanded] = useState(false)
  const features = topFeatures(tickets, 5)
  if (features.length === 0) return null
  const shown = expanded ? features : features.slice(0, 3)
  const more = features.length - 3
  return (
    <div className="feature-breakdown">
      <div className="feature-breakdown__title">Features in progress</div>
      <div className="feature-breakdown__rows">
        {shown.map(f => {
          const pct = f.total ? Math.round((f.qaed / f.total) * 100) : 0
          return (
            <div key={f.key} className="feature-breakdown__row">
              <span className="feature-breakdown__name" title={f.title}>{f.title}</span>
              <span className="feature-breakdown__count">{f.total} ticket{f.total === 1 ? '' : 's'}</span>
              <div className="feature-breakdown__bar" title={`${pct}% QAed`}>
                <div className="feature-breakdown__bar-fill" style={{ width: `${pct}%` }} />
              </div>
              <span className="feature-breakdown__cov">{f.qaed}/{f.total} QAed</span>
            </div>
          )
        })}
      </div>
      {more > 0 && (
        <button className="feature-breakdown__toggle" onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Show less' : `Show ${more} more`}
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Wire it into `App.tsx`**

In `frontend/src/App.tsx`, add to the component-import group (after `import Settings from './components/Settings'`):
```tsx
import FeatureBreakdown from './components/FeatureBreakdown'
```
Insert the card between `</ActiveLanes>` and `<Queue` (after the `ActiveLanes` closing tag, ~`:1097`):
```tsx
      <FeatureBreakdown tickets={tickets} />
```

- [ ] **Step 3: Add CSS**

In `frontend/src/styles/layout.css`, append (mirrors the `.done-today`/`.lane-card` token conventions):
```css
/* ─── Feature Breakdown (hero) ──── */
.feature-breakdown {
  padding: 0 24px 16px;
}
.feature-breakdown__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 8px;
}
.feature-breakdown__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.feature-breakdown__row {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 8px 14px;
  box-shadow: var(--shadow-sm);
  font-size: 12px;
}
.feature-breakdown__name {
  flex: 1;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.feature-breakdown__count {
  color: var(--text-dim);
  white-space: nowrap;
}
.feature-breakdown__bar {
  width: 120px;
  height: 8px;
  background: var(--border);
  border-radius: 999px;
  overflow: hidden;
}
.feature-breakdown__bar-fill {
  height: 100%;
  background: var(--success);
  border-radius: 999px;
}
.feature-breakdown__cov {
  color: var(--text-dim);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  min-width: 64px;
  text-align: right;
}
.feature-breakdown__toggle {
  margin-top: 8px;
  background: none;
  border: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
```

- [ ] **Step 4: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds. Manual (after backend restart): the hero card sits above the Queue, lists the top 3 epics (e.g. "Add Location Email field — 7 tickets — X/7 QAed") with coverage bars and a "Show 2 more" toggle expanding to 5. **#6 demoed.**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FeatureBreakdown.tsx frontend/src/App.tsx frontend/src/styles/layout.css
git commit -m "feat(#6): FeatureBreakdown hero card (top epics + QA coverage)"
```

---

## Task 8: Full sweep + integration smoke

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_github_client.py`
Expected: new Cluster B tests pass; only the documented pre-existing WinError failures (test_chat/council/quartermaster) remain — confirm no NEW failures.

- [ ] **Step 2: Frontend build**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Live smoke (after restarting the backend so the new Linear fields + difficulty are served)**

Restart the backend. `GET /api/tickets` now carries `parent`, `labels`, `createdAt`, `priorityValue`, `difficulty`, `difficultyScore`. In the UI: the hero card shows top epics with coverage; Sort → Difficulty / Oldest first reorders; Group by Epic shows the Location-Email epic + Ungrouped, Group by Label shows Back-End/Front-End/…

- [ ] **Step 4: Final commit (if any cleanup)**

```bash
git add -A && git commit -m "test(cluster-b): full sweep + integration smoke notes"
```

---

## Self-review (author)

- **Spec coverage:** #6 → Tasks 5 (`topFeatures`) + 7 (FeatureBreakdown). #7 → Tasks 1 (difficulty) + 2 (createdAt/priority) + 3 (enrichment) + 6 (sorts). #8 → Tasks 2 (parent/labels) + 5 (`groupTickets`) + 6 (group-by UI). Ticket-type plumbing → Task 4.
- **Type consistency:** backend `difficulty`/`difficultyScore` (Tasks 1/3) match the TS `Ticket` fields (Task 4) consumed by Queue sort (Task 6); `parent {key,title}`/`labels[]`/`createdAt`/`priorityValue` produced by `_map_issue` (Task 2) match the TS `Ticket` (Task 4), `groupTickets`/`topFeatures` (Task 5), and the Queue/Hero consumers (Tasks 6/7). `groupTickets(tickets, by)` and `topFeatures(tickets, n)` signatures are identical across Tasks 5/6/7.
- **Invariants:** difficulty is server-side + tested (Task 1); grouping uses first-label + Ungrouped (Task 5); hero rolls epic-less under label (Task 5); no new endpoints/deps; Jira mapper untouched (Linear-only, per spec).
- **Known data realities (not bugs):** priority mostly unset → Priority sort near-flat; difficulty is heuristic. Both documented in the spec's Open risks.
