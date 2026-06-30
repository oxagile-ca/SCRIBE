# Design — Gated deterministic API smoke (run the Postman collection for API tickets)

**Status:** Approved design (2026-06-29). Ready for an implementation plan.
**Context:** Today the Postman collection (`~/.scribe/xinventory-api.postman_collection.json`,
61 requests / 21 GETs) is parsed only at onboarding to *count* endpoints
(`onboarding._parse_postman_endpoints` → `config.api.postmanCollectionPath`). Nothing
ever executes it: it is not wired into `qa_targets`/`qa_runner`/`qa_orchestrator`,
`newman` is not installed, and the qa-evidence skill's Phase 2.7 only does ad-hoc
in-browser checks at the agent's discretion (which is why API verification gets
skipped). See memory `scribe-postman-not-executed`.

## 1. Goal & non-goals

**Goal:** when a ticket actually changes the API, the backend deterministically runs the
relevant collection requests against the live non-prod env and surfaces the results as
evidence test cases (`TC-API-*`) in the same per-run dashboard report — without relying on
the LLM agent to choose to do it. UI tickets (e.g. INV-675) gate out and are unaffected.

**Non-goals (YAGNI):** mutation testing (POST/PUT/DELETE), `newman`, running the whole
collection every run, LLM-based classification, executing Postman JS pre-request/test
scripts, a standalone "Run API suite" UI, scheduling.

## 2. Decisions (locked during brainstorming)

| Decision | Choice |
|---|---|
| When it runs | **Only for API-relevant tickets**, not every QA run |
| Where it runs | **Deterministic backend step** in `qa_orchestrator.run_and_finalize` (after the agent run), code-driven — no agent discretion |
| Gate | **Hybrid**: heuristics on Linear label + ACs + description first; **PR diff fallback** only when unclear |
| Classifier | **Pure heuristics** (no LLM) |
| Runner | **Python `requests`** reusing a shared collection parser (not `newman`) |
| Scope of requests | **GET / read** requests only, restricted to the endpoints the ticket touches; mutations skipped |
| Strictness | 2xx → **pass**; 5xx / timeout / connection → **fail**; 4xx → **needs-review** (likely a stale fixture ID after a reseed, not a server regression) |
| Auth | Mint a Cognito `id_token` via existing `qa_auth.mint_tokens()`; inject as bearer; token never written to evidence |

## 3. Architecture

Three new, single-purpose modules plus one integration point. Each is independently
unit-testable with a fixture mini-collection and mocked HTTP.

### 3.1 `qa_postman.py` — collection → runnable requests
```
load_requests(path: str) -> list[Request]
```
`Request = {method, name, group, url, query: dict, headers: dict, body, raw}`. Resolves
`{{BASE_URL}}` and other collection variables. Richer than onboarding's count-only
`_parse_postman_endpoints` (left as-is; a later refactor may share a parser — out of scope
here). Returns all requests; callers filter by method/path.

### 3.2 `qa_api_gate.py` — is this an API ticket, and which endpoints?
```
classify(label: str|list, acs: list[str], description: str,
         collection_groups: list[str]) -> Gate
endpoints_from_diff(pr_diff: str) -> list[str]
Gate = {is_api: bool, endpoints: list[str], unclear: bool, source: "label"|"text"|"none"}
```
`classify` is **pure** (no I/O — no diff fetch). Heuristics, in order:
1. **Label** ∈ {`backend`, `api`, `services`, …} → `is_api=True, source="label"`.
2. **Text** (ACs + description) contains an `/api/v1/…` path, an HTTP verb token
   (GET/POST/PUT/DELETE) next to a path, or a known collection **group name** →
   `is_api=True, source="text"`; extracted paths become `endpoints`.
3. Neither hits, but the ticket isn't obviously UI (no UI-only signal) → `is_api=False,
   unclear=True` — a signal for the orchestrator to try the diff fallback.
4. Obvious UI/other ticket → `is_api=False, unclear=False` (smoke skipped, no diff fetch).

`endpoints_from_diff(pr_diff)` (called by the orchestrator only when `unclear`) returns
paths found under route/controller/handler files in the API/services repo. All `endpoints`
are normalized API paths matched against the parsed collection in §3.3.

### 3.3 `qa_api_smoke.py` — run the matched reads, assert, write evidence
```
async run(ticket_key, run_name, api_base, endpoints, *, model=None) -> list[TCResult]
```
1. `mint_tokens()` → `id_token` (skip with a single `blocked` TC-API note if no
   `environments.testAuth` creds configured).
2. From `load_requests(postmanCollectionPath)`, select **GET** requests whose path is in
   `endpoints` (substring/normalized match against the parsed collection paths).
3. Fire each against `api_base` with the bearer + baked-in example params, ~10s timeout.
4. Map status → strictness (§2: 2xx→pass, 5xx/timeout→fail, 4xx→needs-review). Write
   `automated/TC-API-<group>-<n>/api-<name>.json`:
   `{request: {method, path, query}, status, ok, responseKeys: [...], asserted: {...}}`,
   **PII-scrubbed** (email/SSN/card), token never included.
5. Return one `TCResult{id, title, status, note, evidence}` per request.

## 4. Integration point — `qa_orchestrator.run_and_finalize`

After the agent run produces `summary.json` (current line ~94-101), before/around the
existing `generate_html_report(ticket_key, run_name)`:

```
gate = qa_api_gate.classify(label, acs, description, collection_groups)  # pure, no I/O
endpoints = gate.endpoints
if gate.unclear:                                            # only now do diff I/O
    endpoints = qa_api_gate.endpoints_from_diff(fetch_pr_diff(ticket))
if (gate.is_api or endpoints):
    tcs = await qa_api_smoke.run(ticket_key, run_name, api_base, endpoints)
    merge_api_tcs_into_summary(ticket_key, run_name, tcs)   # append to test_cases,
                                                            # recompute verdict/score
generate_html_report(ticket_key, run_name)                  # renders TC-API-* as usual
```
Ticket label/ACs/description and `api_base` come from `qa_targets` (already fetches Linear
scope + `api_base`); the PR diff is fetched only on the unclear branch. Gating out is a
no-op — the run is identical to today. `generate_html_report` already renders
`summary.json.test_cases` + evidence, so no report changes are needed, and the appended
`TC-API-*` entries satisfy Phase 8's existing gap gate ("every TC citing an endpoint must
have an `api-*.json`").

## 5. Data flow

```
qa_targets (label, ACs, desc, api_base, pr ref)
      │
      ▼
qa_api_gate.classify ──unclear──► endpoints_from_diff(pr_diff)
      │ is_api + endpoints
      ▼
qa_api_smoke.run ── mint_tokens() ──► live GET reads ──► api-*.json + TCResult[]
      │
      ▼
merge into summary.json.test_cases (+ recompute verdict/score)
      │
      ▼
generate_html_report → dashboard report shows TC-API-* alongside UI TCs
```

## 6. Error handling

- No `testAuth` creds → single `TC-API` with `status:"blocked"`, note "API auth not
  configured"; run continues (does not crash finalize).
- No `postmanCollectionPath` configured → skip the smoke silently (log once).
- Mint failure / network down → `blocked` TC-API with the error class; not a hard `fail`.
- A request timeout/5xx → that TC `fail`; the run verdict downgrades. Unexpected 4xx →
  `needs-review`. All other TCs still run (one bad endpoint never aborts the batch).
- The whole smoke is wrapped so any exception degrades to a logged `blocked` TC rather than
  failing `run_and_finalize`.

## 7. Verdict/score effect

`merge_api_tcs_into_summary` appends `TC-API-*` to `test_cases` and recomputes
`score`/`verdict` with the same rules the report already uses: any `fail` → verdict
downgrades (PASS → PASS-WITH-ISSUES or FAIL per existing thresholds); `needs-review` is
advisory (no hard downgrade); `blocked` follows existing blocked handling.

## 8. Testing

- `qa_postman.load_requests`: parses a fixture mini-collection; resolves `{{BASE_URL}}`;
  returns method/path/query.
- `qa_api_gate.classify`: label hit; `/api/v1/...` path in AC; verb+path in description;
  group-name hit; **unclear → diff** escalation; UI ticket → `is_api=False`.
- `qa_api_smoke.run`: mocked HTTP — 2xx→pass, 5xx/timeout→fail, 4xx→needs-review,
  no-creds→blocked; asserts `api-*.json` shape + PII scrub + no token leakage.
- `qa_orchestrator`: API ticket → `TC-API-*` land in `summary.json` and verdict recomputes;
  UI ticket → run untouched (gate-out is a no-op).

## 9. Files

| File | Change |
|---|---|
| `backend/qa_postman.py` | NEW — collection parser → runnable requests |
| `backend/qa_api_gate.py` | NEW — heuristic API-ticket gate + diff fallback |
| `backend/qa_api_smoke.py` | NEW — mint token, run reads, assert, write evidence |
| `backend/qa_orchestrator.py` | wire gate+smoke after the agent run; add `merge_api_tcs_into_summary` |
| `backend/qa_targets.py` | expose label/ACs/description + `api_base` (+ pr diff ref) if not already returned |
| `backend/tests/test_qa_postman.py`, `test_qa_api_gate.py`, `test_qa_api_smoke.py`, `test_qa_orchestrator.py` | NEW / extended |

## 10. Open risks

- **Stale example IDs** after a non-prod reseed → 4xx; handled as `needs-review`, not a hard
  fail (so a reseed doesn't flag false regressions).
- **Bucket/tenant scope**: the minted user (workabee-dev, bucket 2) may not have access to
  every baked-in `bucket_id` (e.g. 3) → 4xx `needs-review`. Acceptable; documented.
- **Endpoint→request matching** is path-based and may over/under-match on shared prefixes;
  unit tests pin the matcher. PR-diff endpoint extraction is best-effort (last resort).

## 11. Related
- Memory: `scribe-postman-not-executed`, `xinventory-api-postman`, `xin-np-programmatic-auth`,
  `scribe-evidence-report-fixes`. Built on `feat/headless-qa-phase2`.
