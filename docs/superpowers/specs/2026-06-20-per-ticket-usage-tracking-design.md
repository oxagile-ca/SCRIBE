# Per-Ticket Token & Cost Tracking — Design Spec

**Date:** 2026-06-20
**Status:** Approved (design)
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)
**Related:** [`2026-06-19-per-task-model-switching-design.md`](2026-06-19-per-task-model-switching-design.md)

## Goal

Show **tokens and money (USD) spent per ticket**, broken down **per task and per
model**, so SCRIBE has a durable baseline of AI spend. This ships **before** the
model-switching change so the Haiku before/after is provable (the ledger's `model`
field changes over time and the cost moves with it).

## Why now

Per-ticket spend exists today only partially: evidence-generation `$` shows via OTEL
(`EvidenceHistory.tsx`), chat `$` shows live per-message but isn't persisted
(`chat.py:154`), and the **Council reviewers' `result` event is thrown away entirely**
(`council.py` reads only `assistant` text) — so the **Code Reviewer**, the one task we
deliberately keep on the expensive model, has **invisible** cost. No token counts are
captured anywhere; everything is USD-only.

## Decisions (locked during brainstorming)

1. **Granularity:** per-task **and** per-model breakdown, plus a ticket total.
2. **Persistence:** append-only JSONL ledger.
3. **Source scope:** newly capture **Council** (qa-evidence reviewer + code-reviewer)
   and **Chat** into the ledger with tokens+model; per-ticket total also folds in the
   **existing OTEL** evidence-generation `$` (no tokens for evidence yet).
4. **Display:** all four surfaces — Council panel breakdown, per-ticket breakdown
   detail, lane-card total badge, global dashboard total.

## The data source

`claude -p --output-format stream-json` ends each turn with a terminal event:

```json
{"type":"result","total_cost_usd":0.0123,"duration_ms":8421,"is_error":false,
 "session_id":"...","usage":{"input_tokens":1234,"output_tokens":567,
 "cache_creation_input_tokens":0,"cache_read_input_tokens":0}}
```

`chat.py:154` already reads `total_cost_usd` from this event (but drops `usage`).
`council.py`'s read loop ignores this event type completely. The model id is also
available earlier in the stream's `system` init event (`event.get("model")`).

## Architecture (5 isolated units)

### Unit 1 — Usage ledger module: `backend/usage_ledger.py` (new)

Single owner of the ledger file and all reads/writes. Mirrors the
`council-audit.jsonl` / OTEL JSONL patterns already in the codebase.

- **File:** `~/qa-dashboard/usage-ledger.jsonl` (append-only, thread-locked like
  `council.py`'s `_AUDIT_LOCK`).
- **`record(...)`** appends one line:
  ```json
  {"ts":"2026-06-20T14:03:11Z","ticket":"INV-585","pipeline_id":"...",
   "task":"code-reviewer","model":"claude-sonnet-4-6",
   "input_tokens":1234,"output_tokens":567,
   "cache_creation_input_tokens":0,"cache_read_input_tokens":0,
   "cost_usd":0.0123,"duration_ms":8421,"is_error":false,"session_id":"..."}
  ```
  `ticket` / `pipeline_id` are nullable (chat has none — see Open considerations).
  `task` ∈ `{"qa-evidence","code-reviewer","chat"}`. `model` is the concrete id read
  from the `system` init event when present, else the configured value, else
  `"default"`.
- **`aggregate_for_ticket(key)`** reads the ledger, filters by `ticket`, groups by
  `(task, model)`, sums tokens + cost. Returns the per-task rows + ledger subtotal.
- **`summary(window)`** totals across all records (today / all-time) for the dashboard.

### Unit 2 — Council capture: `backend/council.py`

- In `_run_reviewer`'s read loop (currently lines 110–115, only `assistant`), add
  branches to capture the `system` init `model` and the terminal `result` event's
  `total_cost_usd` + `usage` + `duration_ms` into locals.
- Add those fields to the returned outcome dict (currently lines 140–146):
  `cost_usd, input_tokens, output_tokens, cache_*, duration_ms, model`.
- `_synthesize` (lines 171–226) carries each reviewer's usage into its
  `reviewers_summary` entry so the council payload exposes a per-reviewer breakdown.
- In the `_runner` (after `_synthesize`, lines ~317–331), call
  `usage_ledger.record(...)` once per reviewer with `ticket_key` + `pipeline_id` +
  `task=reviewer.name`, and include a `cost_usd` total on the persisted
  `councilPayload`.

### Unit 3 — Chat capture: `backend/chat.py`

- In the `result` branch (lines 154–162) also read `event.get("usage", {})` for the
  token fields, and capture `model` from the `system` init event.
- On the terminal result, call `usage_ledger.record(task="chat", ticket=None,
  model=CHAT_MODEL_or_actual, ...)`. (Yielded shape to the UI is unchanged + gains
  optional token fields.)

### Unit 4 — Per-ticket aggregation (combines ledger + OTEL)

A backend helper (in `usage_ledger.py` or a thin `usage.py`) computes a ticket's full
picture: `aggregate_for_ticket(key)` (Council + Chat tokens & `$`) **plus**
`_otel.total_cost_for_ticket(runs_path)` as an `evidence-runs` row (`$` only;
`input/output_tokens = null`). Returns per-task rows + a grand total.

### Unit 5 — API endpoints: `backend/server.py`

- **`GET /api/usage/ticket/{key}`** → `{ticket, tasks:[{task,model,input_tokens,
  output_tokens,cost_usd}], total_cost_usd, total_input_tokens, total_output_tokens}`.
  (`evidence-runs` row carries null tokens.)
- **`GET /api/usage/summary`** → `{today:{cost_usd,...}, allTime:{cost_usd,...}}` for
  the dashboard badge.
- **Extend `GET /api/council/{pipeline_id}`** (line 589) to surface the per-reviewer
  usage already on `councilPayload` (no extra round-trip for the council panel).

### Frontend display: `frontend/src/`

- **`components/CouncilPanel.tsx`** — per-reviewer row gains `model · tokens · $`
  (from the extended council payload). *This is where Code Reviewer cost finally shows.*
- **`components/AgentDetail.tsx`** — per-ticket breakdown table (task · model · tokens
  · $ + ticket total), fed by `GET /api/usage/ticket/{key}`.
- **`components/LaneCard.tsx`** — compact `$total` (and token count) badge per ticket.
- **`components/TopBar.tsx`** — global "spend today / all-time" figure from
  `GET /api/usage/summary`.
- **`api.ts`** + **`types.ts`** — client calls + `TicketUsage` / `UsageSummary` types.

## Verification

- **Unit:**
  - `usage_ledger.record` then `aggregate_for_ticket` returns correct grouped
    sums; missing/`null` ticket excluded from per-ticket rollups.
  - council `_run_reviewer` parses a synthetic stream with a `result` event →
    outcome carries `cost_usd` + token fields; a stream with no `result` →
    fields default to `0`/`None` and verdict parsing is unaffected.
  - chat result branch populates token fields from `usage`.
- **Live smoke:** run a council on a known ticket → ledger gains two lines
  (qa-evidence, code-reviewer) with non-zero cost+tokens; `GET /api/usage/ticket/{key}`
  returns a total that includes the OTEL evidence `$`; CouncilPanel shows the
  Code Reviewer's cost; lane badge + TopBar total render.

## Open considerations

- **Chat ticket attribution:** `POST /api/chat/send` (`server.py:797`,
  `ChatSendRequest`) carries no ticket, so chat records use `ticket=null` and roll into
  the **global** total + a "chat" bucket only — not a specific ticket. (Future: add an
  optional `ticket` to the chat request when the panel is opened in ticket context.)
- **Evidence tokens:** evidence-generation contributes `$` only (via OTEL); token
  counts for it stay absent until/unless we parse OTEL token fields (deferred — was
  the "unify all three" option we did not pick).
- **Pre-switch baseline:** until model-switching lands, every task records
  `model="default"` (concrete id resolved from the `system` event when available);
  after the switch, qa-evidence + chat records flip to `claude-haiku-4-5`, making the
  delta visible in the ledger.

## Out of scope

- Moving evidence-generation off OTEL into the ledger.
- Per-user spend breakdown.
- Budget caps / alerting on spend.
- The model switch itself (separate, already-approved spec).
