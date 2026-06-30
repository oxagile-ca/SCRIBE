# Design — Deterministic QA scoring policy (score on ACs + console/network only)

**Status:** Approved design (2026-06-29). Foundational — build first.
**Sub-project 1 of 3** (then main reconciliation, then gated API smoke).

## 1. Problem

Today the QA **score/verdict is written by the LLM agent** into `summary.json`
(`score:{pass,fail,blocked,total,pct}`, `confidence:{headline}`), and it mixes
acceptance-criteria verification with supplementary scans. So an incomplete or skipped
supplementary scan (AXE/a11y could not run headless, no snapshot baseline, the API smoke
was skipped) **drags the headline down even though the feature's ACs are met** — and,
being agent-authored, the number isn't enforceable. We want the score to reflect *whether
the ticket's acceptance criteria hold*, computed deterministically.

## 2. Policy (locked)

A test case is **scoring** or **advisory**:

| Class | Test cases | Scoring? |
|---|---|---|
| AC-tied functional | the ticket's `TC-<KEY>-NNN` (trace to an AC / PR-diff hunk) | **yes** |
| Console scan | `TC-UV-1` | **yes** (catches PR-introduced runtime errors) |
| Network scan | `TC-UV-2` | **yes** (catches PR-introduced 4xx/5xx) |
| Assets | `TC-UV-3` | advisory |
| Doc lifecycle | `TC-UV-4` | advisory |
| Accessibility (AXE) | `TC-UV-5` | advisory |
| Snapshot drift | `TC-UV-6` | advisory |
| API smoke | `TC-API-*` | advisory |

- **Score/verdict are computed from the scoring set only.** Advisory TCs render in the
  report (with their real statuses) but **cannot lower the headline**, and being
  `skipped`/`incomplete`/`blocked`/`needs-review` never penalizes.
- The **backend's computed score is authoritative** — it overrides whatever number the
  agent wrote in `summary.json`, so the policy holds regardless of agent behavior.

## 3. New module — `qa_scoring.py`

```
classify_tc(tc_id: str) -> "scoring" | "advisory"
compute_score(test_cases: list[dict]) -> Score
Score = {
  pass, fail, blocked, total, pct,        # over the SCORING set only
  verdict,                                 # PASS | PASS-WITH-ISSUES | FAIL | BLOCKED
  scoring_ids: [...], advisory_ids: [...]  # for the report's two sections
}
```
- `classify_tc`: `TC-UV-1`/`TC-UV-2` and any non-`UV`/non-`API` id → scoring; `TC-UV-3..6`
  and `TC-API-*` → advisory. (Deterministic, id-pattern based.)
- `compute_score`: tally only scoring TCs. `pct = round(100*pass/total)` over scoring TCs
  (advisory TCs excluded from numerator AND denominator). Verdict mapping:
  - any scoring `fail` → `FAIL` if pass-rate < fail_threshold else `PASS-WITH-ISSUES`
  - all scoring `pass` (no fail/blocked) → `PASS`
  - scoring `blocked` present, no fail → `BLOCKED` (existing blocked handling)
  (Reuse the existing PASS/PASS-WITH-ISSUES thresholds the report already uses; this spec
  centralizes them in `qa_scoring`.)

## 4. Integration

- **`qa_orchestrator.run_and_finalize`** (after the agent run, after any API-smoke/recon
  merges): call `compute_score(summary.test_cases)` and **overwrite** `summary.json`'s
  `score` + `verdict` with the canonical result; persist `scoring_ids`/`advisory_ids`.
- **`agents.generate_html_report`**: render two groups — **Scored** (AC + UV-1/2) and
  **Advisory (not scored)** (UV-3/4/5/6 + API) — and drive the headline ring from the
  canonical `score`. (The report already reads `summary.test_cases`; this adds the
  scoring/advisory split + a label.)
- **`agents.check_evidence`**: already derives the dashboard number from `summary.score.pct`
  → now reflects the canonical score automatically.

## 5. Confidence

`confidence.headline`/`explanation` remain agent-authored *prose*, but the **dashboard's
displayed number is the canonical `score.pct`** (§4), so advisory incompleteness can't move
the headline. The skill is updated to stop docking confidence for advisory scans (a doc
change in the three SKILL.md files / template), but enforcement does not depend on it.

## 6. Error handling

- `summary.test_cases` missing/empty → `compute_score` returns `total:0`, `verdict` falls
  back to the agent's `verdict` if present else `BLOCKED`; never crashes finalize.
- An unknown TC id (neither UV nor API pattern) → treated as **scoring** (fail-safe: a real
  AC-tied TC is never silently dropped from the score).

## 7. Testing

- `classify_tc`: UV-1/UV-2 → scoring; UV-3/4/5/6 → advisory; TC-API-* → advisory; AC id →
  scoring; unknown id → scoring.
- `compute_score`: advisory fails/skips don't change pct or verdict; a scoring (AC or UV-1)
  fail does; all-pass → PASS; blocked handling; empty list.
- Integration: an AXE `fail` + API `fail` on an otherwise all-AC-pass run → canonical
  `PASS` (advisory ignored); a `TC-UV-1` console `fail` → `PASS-WITH-ISSUES`/`FAIL`.
- Backend score overrides a wrong agent-written `score`.

## 8. Files

| File | Change |
|---|---|
| `backend/qa_scoring.py` | NEW — `classify_tc`, `compute_score` |
| `backend/qa_orchestrator.py` | recompute + overwrite `summary.json` score/verdict on finalize |
| `backend/agents.py` | `generate_html_report` scored/advisory split + canonical headline |
| live skills + `backend/templates/qa-evidence.skill.base.md` | stop docking confidence for advisory scans (doc) |
| `backend/tests/test_qa_scoring.py`, `test_qa_orchestrator.py`, `test_evidence_report.py` | NEW / extended |

## 9. Out of scope (YAGNI)

Per-AC weighting, configurable scoring sets, recomputing the agent's confidence *prose*,
changing which scans run (only which ones *score*).

## 10. Related
- Consumed by `2026-06-29-gated-api-smoke-design.md` (TC-API-* must be advisory here) and
  `2026-06-29-main-reconciliation-design.md` (its AC-value divergence is AC-tied → scoring).
- Memory: `scribe-evidence-report-fixes`, `scribe-postman-not-executed`.
