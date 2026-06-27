# Cluster C — QA Automation (Server-side Run + Auto Mode) — Design Spec

**Date:** 2026-06-25
**Status:** Draft (design) — pending user approval
**Repo:** SCRIBE (`C:\Users\ankit\SCRIBE`)
**Target:** web version (a future desktop wrap is out of scope and unaffected)
**Demo:** client demo for Beeventory, week of 2026-06-29
**Related:** `2026-06-19-per-task-model-switching-design.md`, `2026-06-20-per-ticket-usage-tracking-design.md`

## Scope

This is the first of three feature clusters for the demo. It covers two of the
eleven requested improvements:

- **#9 — Close the copy-paste gap.** Today the dashboard builds a `/qa-evidence-…`
  command and tells the user to *paste it into a Claude Code terminal*. Replace that
  with a one-click, server-side run.
- **#10 — Auto mode.** The dashboard picks tickets, runs QA, generates an HTML report,
  exports it to PDF, and (gated) attaches it to Linear with a verdict comment.

Plus one quick win folded in here because it is trivial and demo-relevant:

- **#4 — Hide the header token-cost figures.** (They are *not* hardcoded; they come
  from `/api/usage/summary`. "Remove" = hide the `top-bar__spend` display.)

Out of scope for this cluster (separate specs): Cluster B (ticket UX: hero card,
filters, group-by) and Cluster A (config center). Note Cluster C *consumes* the
`issueTracker.access.write` flag whose editing UI lives in Cluster A (#11); for now
that flag is set via the config file / onboarding.

## Goal

A QA run that today requires a human to copy a command and babysit a terminal becomes:
(a) a single **Run QA** button that runs the whole pipeline server-side and streams
into the existing lane UI, and (b) an **Auto Mode** toggle that loops that pipeline
over eligible tickets — producing a PDF evidence report per ticket and, only when
explicitly armed, attaching it to the live Linear board.

## Why this is feasible in a week

The backend already proves both required patterns:

1. **Spawning `claude -p` unattended.** `council.py` spawns
   `claude -p --output-format stream-json --verbose --permission-mode bypassPermissions`
   via `asyncio.create_subprocess_exec`, parses the `stream-json` events, and streams
   results over SSE (`council.py:51-91`, `:303-380`). #9 is the same mechanism pointed
   at the `/qa-evidence-beeventory` command with a longer timeout.
2. **Forever background loops.** `server.py` startup launches
   `auto_provision.run_loop()` and an env-keepalive loop via `asyncio.create_task`
   (`server.py:98-110`); `auto_provision.py:333-360` is a poll-loop that already picks
   newly-`Ready for QA` tickets. #10 is a sibling loop that runs the #9 path per ticket.

The QA-evidence command and its display already exist
(`agents.py:587-588` builds it; `App.tsx:600-601` shows "Paste this in Claude Code").
We are replacing the *delivery mechanism*, not the skill.

## Decisions (locked during brainstorming)

1. **Approach:** reuse the council subprocess pattern (#9) and the auto_provision loop
   pattern (#10). Do **not** reimplement the QA workflow in the Anthropic SDK, and do
   **not** add a separate worker daemon.
2. **Demo scope:** build the **single-ticket on-demand path first**, then wrap it in a
   **background loop** (the "both" option). #9 is independently demo-able if #10 slips.
3. **Status transitions:** **never** change Linear ticket status. Evidence + verdict
   comment only. Humans keep control of the board.
4. **Write autonomy — double gate (automatic writes only).** An *automatic* Linear
   write (the loop, or a single Run QA that auto-publishes) happens only if **both**:
   - `issueTracker.access.write == true` (the #11 permission flag), **and**
   - a separate **"auto-publish" arming switch** is ON (default **OFF**).
   With write on but auto-publish off (the default), runs produce a local PDF and post
   nothing automatically. This is the safety model for a live client board
   (write is currently `true`). **Separately**, a completed run always offers an
   explicit **"Attach to Linear" button** — a deliberate human click that requires only
   `access.write == true` (not the arm switch), since the human *is* the gate.
5. **Copy-paste fallback:** **Run QA** (server-side) becomes the primary action; the
   copyable command stays available as a hidden/secondary affordance for manual
   debugging. Do not delete the existing copy flow.
6. **PDF engine:** headless **Chrome print-to-PDF** — **no new Python dependency**.
   Chrome is installed at `C:\Program Files\Google\Chrome\Application\chrome.exe`
   (Edge at the standard path is the fallback; both are Chromium). The evidence
   `index.html` is self-contained (base64 images), so it renders offline.
7. **Tracker:** Linear (`issueTracker.type == "linear"`, project `INV`,
   `skillCommand == "/qa-evidence-beeventory"`). Jira attachment is a later fallback,
   not built now.

## Architecture (6 isolated units)

### Unit 1 — `backend/qa_runner.py` (new) — **closes #9**

Single owner of "run the qa-evidence skill server-side." Generalizes
`council._run_reviewer`.

- **Input:** `ticket_key`, `env_url`, optional `model` (defaults to the configured
  QA-Evidence model), `stream_id`.
- **Command:** reuse the existing template from `agents.py:587-588`:
  `{skillCommand} {key} run:qa-feature env:{env} --headless --auto-approve`, wrapped as
  `claude -p "<command>" --output-format stream-json --verbose --permission-mode bypassPermissions [--model …]`.
- **Long-running:** configurable total timeout (default **30 min**,
  `SCRIBE_QA_RUN_TIMEOUT_SEC`) and idle timeout (default 5 min). Council's 300 s total
  is far too short for a browser QA run — this unit sets its own.
- **Streaming:** parse `stream-json` events; forward human-readable progress to the
  lane via the existing `Stream`/SSE infra (`streams.py`); record the terminal `result`
  event (cost/tokens) into the usage ledger exactly as council does.
- **Output:** the evidence run directory (`~/evidence/{key}/runs/{run}`) and a status
  (`completed` / `failed` / `timeout` / `cancelled`).
- **Cancellation:** expose a cancel handle (kill the subprocess tree) so Auto Mode and
  the UI can stop a run.

### Unit 2 — `backend/pdf_export.py` (new)

Single owner of HTML→PDF. No external network, no new pip deps.

- **`export(html_path) -> pdf_path | None`**: run
  `chrome --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf=<out> file:///<html_path>`
  via `asyncio.create_subprocess_exec` with a short timeout.
- **Browser discovery:** Chrome path first, Edge fallback, configurable via
  `SCRIBE_CHROME_PATH`. If none found, return `None` (caller degrades to HTML-only).
- **Output:** `…/runs/{run}/evidence.pdf` next to `index.html`.

### Unit 3 — `backend/linear_writer.py` (new) — **#11 enforcement point**

Single owner of Linear *writes*. Read client (`linear_client.py`) is untouched.

- **`attach_evidence(ticket_key, pdf_path, verdict_summary) -> AttachResult`**:
  1. `fileUpload` GraphQL mutation → returns a signed upload URL + asset URL.
  2. `PUT` the PDF bytes to the signed URL.
  3. `commentCreate` mutation posting the verdict summary (score, confidence, PASS/BLOCK,
     traceability one-liner) with a markdown link to the asset.
- **Gate:** the writer self-checks `issueTracker.access.write == true` and returns
  `skipped(reason)` otherwise (defense-in-depth). The *automatic* path adds the arm
  switch: callers (Unit 4 loop, single-run auto-publish) only invoke the writer when
  `auto_publish_armed` is also ON. The **manual "Attach to Linear"** button invokes the
  writer directly (write-flag only) — the human click is the second gate.
- **Auth:** `LINEAR_TOKEN` from `.secrets.env` (already loaded). Linear personal API
  keys carry write scope; if the call 401/403s, surface clearly and keep the local PDF.
- **Idempotency:** caller records `attached:true` in the run's `infra.json`; the writer
  is a no-op if already attached for that run.

### Unit 4 — `backend/auto_mode.py` (new) — **the #10 loop**

Controller + background loop. Mirrors `auto_provision`'s structure.

- **State:** `enabled: bool`, `auto_publish_armed: bool` (default False),
  `current: ticket_key | None`, `recent: [...]`. Persist in `PipelineStore` so it
  survives backend restarts; expose via API.
- **Eligibility:** tickets whose `statusCategory == "ready_for_qa"`, ordered by
  priority (later: by the Cluster B filters). Skip tickets already QAed today or with a
  run in flight.
- **Concurrency:** respect the **existing env-lock system and the 3-lane cap** — auto
  mode never exceeds available envs/lanes. Default 1 concurrent for a controllable demo,
  configurable up to the lane cap.
- **Per-ticket pipeline:** `qa_runner` (Unit 1) → existing council review → existing
  `generate_html_report()` → `pdf_export` (Unit 2) → **if write && armed**:
  `linear_writer` (Unit 3); else mark "PDF ready (not published)".
- **Resilience:** per-ticket retry cap (like `auto_provision`'s failure counter); a
  failed ticket logs and the loop advances. PDF/attach failures never abort the loop.

### Unit 5 — `backend/server.py` wiring

- `POST /api/qa-run/{key}` — start a single server-side QA run (Unit 1); returns
  `{streamId}` (same shape as `/api/test`).
- `POST /api/qa-run/{key}/cancel` — cancel a run.
- `GET /api/auto-mode` / `POST /api/auto-mode` — read/set `{enabled, autoPublishArmed}`.
- Register the auto-mode loop in the existing `startup()` alongside `auto_provision`.

### Unit 6 — frontend

- **Run QA button** (`LaneCard` / `QueueRow`): primary action calling `/api/qa-run/{key}`,
  streaming into the existing lane log via the current SSE subscription. The old
  "Paste this in Claude Code" line + clipboard copy is demoted to a secondary
  "copy command" affordance (kept, not deleted) per decision #5.
- **Auto Mode control** (header strip near Daily Huddle / Weekly 3×3): a toggle for
  `enabled` and a clearly-separate, default-off **"Auto-publish to Linear"** switch
  (`autoPublishArmed`) with a confirm step, plus a small status line ("Auto mode: ON —
  processing INV-660 (2 of 7)").
- **Manual "Attach to Linear"** on a completed run (lane/evidence row): shown only when
  `access.write == true`; an explicit click that calls `linear_writer` regardless of the
  arm switch. Lets you publish a hand-picked run without arming the loop.
- **#4 cost-hide:** remove the `top-bar__spend` block (`TopBar.tsx:95-100`) and its
  `getUsageSummary` polling. (Per-ticket usage UI elsewhere is untouched.)

## Data flow

**Single-ticket (demo path):**
```
Run QA → qa_runner spawns claude -p (/qa-evidence-beeventory … --headless)
  → stream progress to lane → evidence at ~/evidence/{key}/runs/{run}
  → council review (PASS/BLOCK) → generate_html_report() → pdf_export
  → if access.write && auto_publish_armed: linear_writer (PDF + verdict comment)
     else: lane shows "PDF ready — not published"
```

**Loop (auto mode):**
```
enable → pick next eligible ticket (respect env locks / lane cap)
       → run the single-ticket path → record outcome → repeat
       → stop when queue drained or toggled off
```

## Error handling

- **qa_runner:** total/idle timeouts kill the subprocess tree; status surfaced to lane;
  in auto mode, count toward the per-ticket retry cap then skip.
- **pdf_export:** missing browser or conversion failure → return `None`; pipeline
  continues and attaches/links the HTML report instead, with a visible warning.
- **linear_writer:** auth/scope failure → keep local PDF, surface error, loop advances.
- **Idempotency:** `infra.json` records `attached:true` per run; no double-posting.
- **Restart safety:** auto-mode state in `PipelineStore`; in-flight single runs follow
  the existing pipeline-resume behavior.

## Testing

- **Unit:** qa_runner command builder (exact string), the double-gate decision table
  (write × armed → attach/skip), pdf_export browser-discovery + arg construction,
  linear_writer payload construction (mocked GraphQL/HTTP).
- **Dry-run mode:** `SCRIBE_QA_FAKE_CLAUDE` points `qa_runner` at a stub that emits a
  canned `stream-json` transcript, so the full orchestration (loop → report → pdf →
  gate) is testable without burning tokens or hitting a browser.
- **Integration (manual, pre-demo):** one real INV ticket end-to-end with auto-publish
  **off** (verify local PDF), then one with it **armed** against a throwaway/test issue
  to confirm the Linear attach + comment.

## Build order

1. **#4** hide header cost (minutes).
2. **Unit 1 + Run QA button** → **#9 complete and demo-able.**
3. **Unit 2** pdf_export.
4. **Unit 3 + double gate** linear_writer.
5. **Unit 4 + Auto Mode UI** → **#10 complete.**

## Open risks

- **Unattended headless QA reliability.** A `claude -p /qa-evidence-beeventory …
  --headless` run drives browser automation for many minutes. The skill is already
  adapted for headless capture (DOM-capture + PIL render per prior runs), but a
  long unattended run is the main technical risk. Mitigation: validate with a real
  single-ticket run early (step 2), before building the loop.
- **Linear file-upload flow.** The `fileUpload`→PUT→`commentCreate` sequence needs a
  quick spike against the real API to confirm asset linking renders in a comment.
