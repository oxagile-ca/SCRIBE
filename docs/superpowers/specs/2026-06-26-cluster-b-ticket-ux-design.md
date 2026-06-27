# Cluster B — Ticket UX (Hero Breakdown / Filters / Group-by) — Design Spec

**Date:** 2026-06-26
**Status:** Draft (design) — pending user approval
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)
**Branch:** `feat/scribe-demo-clusters` (the unified branch already containing Clusters C + A; B lands here so the branch = A+B+C)
**Target:** web version; tracker = Linear (project INV / "Beeventory HMS MVP")
**Demo:** Beeventory client demo, week of 2026-06-29
**Related:** `2026-06-25-cluster-c-automation-design.md`, `2026-06-26-cluster-a-config-center-design.md`

## Scope

The third demo cluster. Three of the eleven improvements:

- **#6 — HERO breakdown card** of the features being worked on: top 3 features, expandable to 5.
- **#7 — Ticket sort orderings:** Easy→Hard (difficulty), Priority, Oldest→New.
- **#8 — Group-by:** group tickets by their parent/epic or by label (feature/type).

Out of scope: Clusters C (automation) and A (config center) — already built on this branch.

## Goal

The QA queue gains a feature-level view: a hero card summarizing the top epics being worked on (with QA coverage), the ability to sort the queue by difficulty / priority / age, and the ability to group the queue by epic or by label — so a tester can see "what features are in flight and how covered they are" and slice the queue accordingly.

## Why this is feasible / grounded in the live board

A 30-ticket probe of the live INV board established the data reality:

- **parent (epic): ~43% populated** — real features (e.g. epic INV-654 "Add Location Email field" has 7+ children). Good grouping key.
- **labels: 100%** — `Back-End` / `Front-End` / `Design` / `Infrastructure/DevOps` / `Bug`. The "type" dimension.
- **project: single** ("Beeventory HMS MVP v1.0.0") — useless for grouping, dropped.
- **estimate (story points): 0% used** — so difficulty has NO native source → derived heuristic.
- **priority: essentially unset** ("No priority") — Priority sort will be mostly flat; fetching numeric `priority` just makes the few set ones order correctly.

The frontend `Queue.tsx` is already a composable `.filter().filter().sort()` chain with a Sort dropdown (`switch` on sort key) and a render `.map()` — clean seams for new sorts and grouped rendering. `/api/tickets` already has a post-fetch enrichment loop (computes `statusCategory`, `evidence`) — the natural seam for a derived `difficulty`. The structural fields (parent/labels/createdAt/priority) just need adding to the Linear query + `_map_issue` + the TS `Ticket` type.

## Decisions (locked during brainstorming)

1. **Difficulty (#7):** a derived heuristic (story points are unused). Computed server-side from the description: count of acceptance-criteria-style lines + description length → `Easy` / `Medium` / `Hard` + a numeric `difficultyScore` for sorting. Documented as approximate.
2. **Group-by (#8):** a `None / Epic / Label` toggle. Epic = parent issue (title as the group header); Label = the ticket's label. Tickets lacking the key go in an **"Ungrouped"** section (rendered last). Multi-label tickets group under their **first** label (no duplication). Project is not a grouping option.
3. **Hero card (#6):** a "feature" = an **Epic/Parent**. The card shows the **top 3 epics by ticket count** (a "show 2 more" control expands to 5); each row = epic title + ticket count + a **QA-coverage bar** (`X/N QAed`). Tickets without an epic **roll up under their label** as fallback features. Placed between Active Lanes and the Queue.
4. **#7 are SORT orderings**, not filter facets — extend the existing Sort dropdown with **Difficulty** (Easy→Hard) and **Created** (Oldest→New). Priority sort already exists; improve it with numeric priority.
5. **Fetch, don't parse:** parent/labels/createdAt come from Linear fields, not from title bracket-prefixes.
6. **Difficulty is server-side and tracker-agnostic** (testable in pytest); grouping + sort + hero are client-side view concerns (no new endpoints).

## Architecture (units)

### Backend

**Unit 1 — `linear_client.py` field extension.**
Extend `_ISSUES_QUERY` to also select `createdAt`, `parent { identifier title }`, `labels { nodes { name } }`, and numeric `priority`. Extend `_map_issue` to set:
- `createdAt`: the issue's `createdAt` (ISO string; `""` if absent).
- `parent`: `{ "key": <identifier>, "title": <title> }` or `null`.
- `labels`: `[name, …]` (possibly empty).
- `priorityValue`: numeric Linear priority (0=None,1=Urgent,2=High,3=Medium,4=Low) for reliable sorting; keep `priority` (the label) for display.
Not fetched: `estimate` (unused). Jira path is left as-is (parity gap noted under Open risks) — Linear is the active tracker.

**Unit 2 — `ticket_difficulty.py` (new).**
`compute_difficulty(description: str) -> tuple[str, int]` → `(label, score)`.
- `ac_count` = number of acceptance-criteria-style lines in the description (checklist/bulleted/numbered lines, or lines under an "acceptance criteria" heading).
- `score` = `ac_count` (primary signal), with a small bump for long descriptions (e.g. `+1` per ~600 chars beyond the first, capped).
- Buckets: `score <= 2` → `Easy`; `3..5` → `Medium`; `>= 6` → `Hard`. Empty/short description → `Easy`, score 0.
- Pure function, no I/O — fully unit-tested.

**Unit 3 — `server.py` `/api/tickets` enrichment.**
In the existing post-fetch loop (where `statusCategory` and `evidence` are set), add:
`label, score = ticket_difficulty.compute_difficulty(t.get("description", "")); t["difficulty"] = label; t["difficultyScore"] = score`.

### Frontend

**Unit 4 — `types.ts` `Ticket` extension.**
Add: `createdAt?: string`, `parent?: { key: string; title: string } | null`, `labels?: string[]`, `priorityValue?: number`, `difficulty?: 'Easy' | 'Medium' | 'Hard'`, `difficultyScore?: number`.

**Unit 5 — `ticketGroups.ts` (new) — pure helpers.**
- `groupTickets(tickets, by: 'epic' | 'label') -> { key: string; title: string; tickets: Ticket[] }[]` — ordered groups; tickets missing the key collected into a trailing `{ key: '__ungrouped__', title: 'Ungrouped', … }` group; epic groups ordered by size desc, then ungrouped last; label grouping uses the first label.
- `topFeatures(tickets, n) -> { key: string; title: string; total: number; qaed: number }[]` — epics ranked by ticket count (top `n`); each carries `total` + `qaed` (via the existing `isTicketQAed`); epic-less tickets folded into label-named pseudo-features so the card is never empty.
Pure functions (no React) so they're independently reasoned about and unit-testable.

**Unit 6 — `Queue.tsx` extension.**
- Sort dropdown: add `{ key: 'difficulty', label: 'Difficulty' }` (asc = Easy→Hard via `difficultyScore`) and `{ key: 'created', label: 'Oldest first' }` (asc = oldest via `createdAt`); add their `DEFAULT_DIR` entries. Priority comparator uses `priorityValue` when present.
- A **Group by** control (`None | Epic | Label`). When not `None`, render the post-sort tickets as grouped sections via `groupTickets` (group header showing title + count, then the group's rows; "Ungrouped" last). When `None`, current flat behavior is unchanged.

**Unit 7 — `FeatureBreakdown.tsx` (new) — the hero card.**
Consumes `topFeatures(tickets, 5)`. Renders the top 3 rows by default with a "Show 2 more" toggle to 5 (and "Show less"); each row = feature title + `N tickets` + a QA-coverage bar (`qaed/total`) with a percentage. Reuses theme CSS vars; collapses gracefully when there are no tickets.

**Unit 8 — `App.tsx` wiring.**
Render `<FeatureBreakdown tickets={tickets} />` between `ActiveLanes` and `Queue`.

## Data flow

```
/api/tickets → linear_client.get_tickets (now returns parent/labels/createdAt/priorityValue)
  → enrichment loop adds statusCategory + evidence + difficulty/difficultyScore
  → frontend tickets[]
       ├─ FeatureBreakdown: topFeatures(tickets, 5) → top-3/expand-5 epic rows + QA coverage
       └─ Queue: filter → sort (now incl difficulty/created) → groupTickets(by) → sectioned render
```
No new endpoints; the hero card and grouping are pure client transforms over the existing tickets array.

## Error handling

- Missing `parent` → ticket falls into the "Ungrouped" group (and, in the hero card, rolls up under its label).
- Missing/empty `description` → `Easy`, score 0.
- Missing `createdAt` → sorts last under "Oldest first".
- No tickets / no epics → hero card renders a quiet empty state (or hides).
- Linear adds fields without changing pagination; `_map_issue` defaults every new field so a sparse response never throws.

## Testing

- **Backend pytest:** `compute_difficulty` — empty description → Easy/0; few ACs → Easy; 3–5 ACs → Medium; 6+ ACs → Hard; long description bumps the score; and a test that the `/api/tickets` enrichment sets `difficulty`/`difficultyScore` on each ticket (via TestClient or the loop logic).
- **Frontend:** `ticketGroups.ts` pure helpers verified via `npm run build` (tsc) + manual; `groupTickets` ungrouped/first-label behavior and `topFeatures` ranking/roll-up checked manually against the live board. Manual: Sort by Difficulty + Oldest-first reorders the queue; Group by Epic shows the Location-Email epic with its children + an Ungrouped section; Group by Label shows Back-End/Front-End/…; hero card shows top epics with coverage and the 3→5 toggle.

## Build order

1. Unit 2 (`ticket_difficulty.py`) + tests — pure, standalone.
2. Unit 1 (`linear_client` fields) + Unit 3 (enrichment wiring) — backend now serves parent/labels/createdAt/priority + difficulty.
3. Unit 4 (`types.ts`) + Unit 5 (`ticketGroups.ts`) — frontend data layer.
4. Unit 6 (`Queue.tsx` sorts + group-by) — **#7 + #8 land**.
5. Unit 7 (`FeatureBreakdown.tsx`) + Unit 8 (App wiring) — **#6 lands**.

## Open risks

- **Priority is unset on the board.** The Priority sort will be near-flat until tickets get priorities; fetching numeric `priority` is the correct-but-low-yield fix. Not a bug — a data reality; surfaced so it isn't mistaken for one at the demo.
- **Difficulty is heuristic.** It approximates effort from AC count + description length; it will sometimes disagree with human judgment. Documented in the UI tooltip ("estimated from acceptance criteria").
- **Multi-label tickets** group under their first label only (most have a single label). If finer control is wanted later, a label picker can be added.
- **Jira parity:** the new structural fields are added to the Linear adapter only. If the instance ever switches to Jira, epic/labels/created grouping would degrade until the Jira mapper gains the same fields (noted, out of scope).
