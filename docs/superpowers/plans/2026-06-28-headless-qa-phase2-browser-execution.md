# Plan — Headless QA Phase 2: real browser execution via Playwright MCP (Option B)

**Status:** Ready to implement (designed 2026-06-28). Implement in a fresh context.
**Owner hand-off:** start a new session and say: *"Implement docs/superpowers/plans/2026-06-28-headless-qa-phase2-browser-execution.md"*.

---

## 1. Problem & context (read first)

The dashboard's **server-side "Run QA"** button (cluster-C feature #9 — `POST /api/qa-run/{key}` → `backend/qa_runner.py` spawns `claude -p --headless --auto-approve --isolated "/qa-evidence-beeventory <KEY> run:qa-feature env:<url> ..."`) **does not execute any tests**. It builds a manifest (Phase 0–1) and stops, producing no `runs/<run>/summary.json`, so the dashboard never registers a completed run.

**Root cause:** the skill's Phase 2 (`~/.claude/skills/qa-evidence-beeventory/SKILL.md`, step 5) executes tests via `pnpm playwright test --project=evidence --grep @<KEY>`. That harness **does not exist in this deployment**: no `pnpm`, no `playwright.config`, no "evidence" project, no per-ticket specs. So the headless agent finds nothing to run and stops.

**Why this was never noticed:** the 50+ documented tickets were QA'd **interactively** — a human + the agent driving a *real* browser (Claude-in-Chrome on xin-np), exercising the app and documenting findings. Interactively the dead `pnpm` line didn't matter; the agent adapted and drove the browser by hand. The headless `claude -p` path (new this cycle) has no human to adapt and follows the skill literally. **The interactive flow is untouched by this plan.**

**Key enabling facts (verified 2026-06-28):**
- Both `claude-in-chrome` and `playwright` MCP servers are configured in `~/.claude.json`, so the spawned `claude -p` **has the Playwright MCP available** (headless-capable). The skill just never tells it to use it. *(Verify the subprocess actually loads MCP — see Discovery D3.)*
- `--isolated` gives each headless run its own fresh browser → multiple tickets can run **in parallel** (this is the whole point of headless; interactive can't, it shares one Chrome).
- Test data exists: 5 seed bookings on Totem (`BK_X077IUKO`, `BK_9IRRVC4P`, `BK_CP8WAXM4`, `BK_CRKCYYNI`, `BK_J755DICT`), each with a renderable invoice (`GET /so/invoice?booking_id=4` → 200). See `[[beeventory-seed-bookings-via-api]]`.

## 2. Goal

Make the **headless** agent drive the **Playwright MCP** browser to perform real QA — codifying what the interactive agent does by hand — and write the evidence the dashboard expects. Three parts (all in scope, per user):
1. **Phase 2 execution:** login → navigate → screenshot → capture console/network → assert → write `runs/<run>/summary.json` + `index.html`.
2. **Scoping guard:** build the manifest from the ACTUAL ticket scope; never silently adopt a `Related: INV-xxx` link's scope (this caused INV-662 — a location-email ticket — to be tested as invoice rendering).
3. **Booking-aware navigation:** invoice/folio/payment/deposit/checkin/checkout tickets must navigate to an EXISTING seed booking's invoice/folio (via UI: booking detail → Print Invoice), NEVER the Create-Reservation room-grid (which headless Playwright cannot drive — the original INV-653 blocker).

## 3. Decisions already made (do not re-litigate)
- **Auth:** drive the real login form (type test creds), not token injection (injection doesn't log the SPA in) and not pre-captured storageState.
- **Approach:** prescriptive skill steps + a thin deterministic helper (`qa_targets.py`). The helper removes the agent's guesswork (creds, which booking, URLs); the agent does per-TC assertions.
- **Do NOT** build a Playwright "evidence" project / install pnpm (rejected Option A). **Do NOT** touch the interactive flow.

## 4. Output contract (dashboard requirement — verified in `backend/agents.py:1183-1236` `check_evidence`)
A run counts as complete only if BOTH exist in `<evidence_root>/<KEY>/runs/<run-id>/`:
- **`index.html`** — non-zero (the report portal; `generate_html_report` can build it from summary.json, or the skill writes it).
- **`summary.json`** — read for the score. Accepted shapes:
  - `score`: a number, OR a tally dict `{pass, fail, blocked, total, pct, verdict}` (dashboard coerces to `pct`, else `100*pass/total`).
  - fallback `confidence`: a number OR `{headline: <num>}`.
  - `time` or `date`: timestamp string.
  - include `test_cases: [{id, title, status: pass|fail|blocked, evidence: [paths]}]` and a top-level `verdict` string for the report.
- `evidence_root` = `backend/evidence` (i.e. `QA_EVIDENCE_ROOT`); run-id = `run-<kind>-<user-slug>-<seq>`.

## 5. Files to change
| File | Change |
|---|---|
| `~/.claude/skills/qa-evidence-beeventory/SKILL.md` | **Live skill** the headless run loads. Rewrite Phase 2 (browser-MCP sequence); add Phase 0/1 scoping guard; add booking-aware nav rule. |
| `backend/templates/qa-evidence.skill.base.md` | The **template** onboarding regenerates the skill from. Mirror the SAME changes so re-onboarding doesn't reintroduce the bug. **Both files must change.** |
| `backend/qa_targets.py` | **NEW** thin helper: deterministic target resolution (creds, login URL, ticket-type classification, seed-booking selection). |
| `backend/tests/test_qa_targets.py` | **NEW** tests (TDD). |
| (also check) `~/.claude/skills/qa-evidence-xinventory/SKILL.md` | If the xinventory variant shares the same Phase 2, apply the same fix. |

## 6. Component design

### 6a. `qa_targets.py` (deterministic resolver — call once at start of Phase 2)
Run by the agent via Bash: `python qa_targets.py <KEY> <env_url>` → prints JSON:
```json
{
  "login_url": "https://xin-np.wbee.ca/",
  "username": "workabee-dev",
  "ticket_type": "invoice|folio|payment|display|filter|config|other",
  "seed_booking": {"booking_id": 4, "booking_number": "BK_X077IUKO", "invoice_total_due": 13685} | null
}
```
- Reuse `qa_auth.load_test_credentials()` for `username` (do NOT print the password — the agent reads `${secret:TEST_LOGIN_PASSWORD}` from `.secrets.env` itself when typing it; see auth flow).
- `ticket_type`: classify from the ticket summary/description keywords (invoice/folio/payment/deposit/checkin/checkout → booking-dependent; filter/config/display/dashboard/nav → existing-data).
- `seed_booking`: for booking-dependent types, call `POST /booking/search` (auth via `qa_auth.mint_tokens`) and pick a CONFIRMED booking whose `GET /so/invoice?booking_id=N` returns 200 (prefer the QASeed ones). Null otherwise.
- Mirror the booking-API recipe in `[[beeventory-seed-bookings-via-api]]`.

### 6b. SKILL.md Phase 2 rewrite (replace the `pnpm playwright test` block, step 5)
Prescriptive Playwright-MCP sequence (tools: `mcp__plugin_playwright_playwright__browser_navigate / browser_type / browser_click / browser_snapshot / browser_take_screenshot / browser_console_messages / browser_network_requests / browser_evaluate / browser_wait_for`):
1. Run `qa_targets.py` → get creds/url/type/seed_booking.
2. **Auth:** `browser_navigate(login_url)`; inspect page (`browser_snapshot`) to find the username/password fields; `browser_type` username; `browser_type` password (value read from `.secrets.env` `TEST_LOGIN_PASSWORD`, never echoed to logs/evidence); submit; `browser_wait_for` the authenticated dashboard.
3. **Per TC**, navigate to the surface:
   - booking-dependent → open `seed_booking` (search/click into the booking → Print Invoice/Folio modal — see `[[beeventory-folio-surface]]` / `[[beeventory-qa-evidence-INV-573]]`). NEVER the Create-Reservation room grid.
   - existing-data (display/filter/config) → navigate directly to the relevant page.
   - `browser_take_screenshot` → save under `runs/<run-id>/automated/<TC-ID>/`; run assertions via `browser_snapshot`/`browser_evaluate`; mark pass/fail.
4. **Universal Validation Suite** (deterministic): `browser_console_messages` → UV-1; `browser_network_requests` → UV-2; asset/smoke/a11y as feasible.
5. Write `runs/<run-id>/summary.json` (§4 schema) and ensure `runs/<run-id>/index.html` exists (write it, or call the backend's `generate_html_report`).
6. Headless rule (keep existing): on TC failure do NOT pause — mark `fail`, screenshot, continue.

### 6c. Phase 0/1 scoping guard
Add: "Build the manifest from THIS ticket's own title + description + its PR diff. `Related: INV-xxx` links are CONTEXT only — never adopt the related ticket's feature scope. If the ticket's own scope is unclear, test what THIS ticket's description says, not the related ticket."

### 6d. Booking-aware navigation rule
Add a rule keyed off `qa_targets.ticket_type`: booking-dependent tickets MUST use an existing seed booking (from `qa_targets.seed_booking`); if none, STOP with verdict `blocked` + reason (don't attempt room-grid creation).

## 7. Implementation steps (ordered)
1. **Confirm output contract** — re-read `check_evidence` + `generate_html_report` in `backend/agents.py`; confirm exact `summary.json`/`index.html` expectations (§4).
2. **Build `qa_targets.py` test-first** — `test_qa_targets.py`: ticket-type classification + seed-booking selection (mock the booking API) + creds loading (no password leak). Then implement.
3. **Rewrite Phase 2** in BOTH `SKILL.md` (live) and `qa-evidence.skill.base.md` (template). Keep run-id/dir structure + UV suite.
4. **Add scoping guard + booking-aware nav** to both files.
5. **Verify live** (§8).
6. Update `[[scribe-headless-qa-room-grid-blocker]]` memory with the outcome.

## 8. Verification / success criteria
Restart the backend clean (single uvicorn — see `[[scribe-start-procedure]]` crash-loop note), then run two tickets via `POST /api/qa-run/{key}` and watch `backend/evidence/<KEY>/runs/`:
- **INV-662** (display / non-booking): agent logs in → opens dashboard Location Info Card → screenshots → asserts email+website shown → writes `summary.json` + `index.html`. `POST /api/check-evidence/INV-662` returns `status != none` with a score. **No "invoice" manifest** (scoping guard works).
- **An invoice ticket** (e.g. INV-653): agent logs in → opens a SEED booking's invoice (NOT the room grid) → screenshots → asserts balance reconciliation → writes evidence. Dashboard registers it.
- **Parallel check (optional):** fire two `qa-run` POSTs at once; both produce isolated runs (separate browsers, no "browser already in use").
Success = real screenshots in `automated/` + valid `summary.json` + dashboard shows the run with a score, for both a non-booking and a booking ticket.

## 9. Discovery items (resolve during implementation — pointers given)
- **D1 — exact `summary.json`/report schema:** `backend/agents.py` `check_evidence` (§4) + `generate_html_report`.
- **D2 — login UI specifics:** unknown selectors / Cognito hosted-UI vs custom form. Discover on first run via `browser_snapshot` of `login_url`. Creds (`workabee-dev` + `TEST_LOGIN_PASSWORD`) are known-good (qa_auth USER_PASSWORD_AUTH succeeds).
- **D3 — Playwright MCP reachable from `claude -p`?** Configured in `~/.claude.json`, but confirm the spawned subprocess loads MCP servers. If not, pass `--mcp-config` or equivalent in `qa_runner.build_runner_argv` (`backend/qa_runner.py:24`). If MCP truly unavailable headlessly, fallback: have `qa_runner` start the Playwright MCP for the subprocess.
- **D4 — invoice UI nav path:** booking detail → "Print Invoice" modal (`[[beeventory-qa-evidence-INV-573]]`, `[[beeventory-folio-surface]]`), not a direct `/folio` URL.
- **D5 — bot/captcha on xin-np login headless:** if blocked, may need storageState fallback (capture once interactively).

## 10. Risks
- Headless Cognito login may behave differently than interactive (D2/D5). Biggest risk; validate early.
- MCP-in-subprocess (D3) — if unavailable, this approach needs the qa_runner to provision the browser MCP.
- Agent non-determinism — mitigate with prescriptive steps + `qa_targets` removing guesswork; accept some variance.

## 11. Out of scope (YAGNI)
- No Playwright "evidence" project / pnpm install (rejected Option A).
- No change to the interactive QA flow (works).
- No change to the dashboard UI / merged QA-UX feature (works).
- No Linear auto-attach changes (gate already off by default).
