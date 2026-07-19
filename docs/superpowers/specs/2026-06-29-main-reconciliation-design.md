# Design — Main reconciliation (current main is authoritative, not the PR snapshot)

**Status:** Approved design (2026-06-29). Build second (after scoring policy).
**Sub-project 2 of 3.**

> **Implementation status (2026-07-01):** Engine + divergence guard BUILT via TDD (93
> unit tests green, live-validated). New `backend/github_client.py` (PR adapter + gh-API
> shells; token via `gh auth token`, see memory `scribe-gh-token-reconcile`) and
> `backend/qa_reconcile.py` (`reconcile`/`reconcile_live`/`fetch_ticket_pr_refs`/
> `build_reconcile_tcs`). PR→ticket link is resolved from **Linear attachment URLs** (not
> branch names — those often omit the key). Wired into `qa_orchestrator.reconcile_ticket`
> + `run_and_finalize` as a **finalize-time guard** (writes `reconcile.json`, injects
> needs-review `TC-RECON` into `summary.test_cases` before canonical scoring). Refinements
> beyond this doc, from real-data validation: (a) **test/mock/snapshot/story files are
> excluded** (a changed fixture is not a stale AC); (b) scoring TCs are **grouped one per
> file** (a refactored file yielded 24 TCs otherwise). **REMAINING:** §4's pre-agent path —
> running reconcile BEFORE the agent so skill Phase-1 derives ACs from the MAIN snapshot
> (edits the 3 skill files + template) — is NOT yet done; only the guard runs today.

## 1. Problem

QA anchors a ticket's acceptance criteria and expected values to its **PR snapshot**. When a
**later** ticket supersedes a value, re-verifying the old PR still "passes" on the stale
value. Observed: INV-624's PR sets MRDT **3%**; later tickets changed it to **2%**; a re-run
still passed on 3% even though current main says 2%. The PR must not be the sole source of
truth — **current main HEAD is authoritative**, and a PR↔main divergence on a ticket value
must block a false pass. (Recurring lesson across QA notes: *check CURRENT main, not the
PR-anchor diff*; deployed/main has moved on.)

## 2. Decisions (locked)

| Decision | Choice |
|---|---|
| Authority | **current main HEAD** (services + ux repos), not the isolated PR diff |
| Mechanism | **Re-anchor + divergence guard**: build expected values from main; ALSO flag where a later main commit superseded the PR |
| Where it runs | **Deterministic backend pre-step**, BEFORE the agent run (so the manifest is built on reconciled context) |
| Repo access | **gh API** (no local checkout), `ankitguhe-afk` token, services + ux repos |
| Divergence effect | AC-value divergence is **AC-tied → scoring** (per scoring-policy spec): a run cannot PASS on a value main no longer has |

## 3. New module — `qa_reconcile.py`

```
reconcile(ticket_key, prs: list[PRRef]) -> ReconcileResult
ReconcileResult = {
  status: "ok" | "degraded",
  touched_files: [ {repo, path} ],
  main_snapshot: { "<repo>:<path>" -> content@mainHEAD },   # authoritative "now"
  pr_snapshot:   { "<repo>:<path>" -> content@PRhead },
  divergences: [ {repo, path, region, pr_hint, main_hint} ], # PR line changed AGAIN
                                                             # by a later main commit
  degraded_reason: str | None,
}
```
- Inputs: the resolved PR refs from `qa_targets`/`agents._consolidate_prs` (services#NNN,
  ux#NNN), repo = `Workabee-Technologies/<repo>`.
- For each PR: via **gh API**, fetch (a) the PR's changed file list + **PR-head** blob of
  each, and (b) the **main-HEAD** blob of each. Diff the PR-touched regions PR-head↔main-head.
- A **divergence** = a region the PR changed that a *later* commit on main changed again
  (main-head ≠ PR-head within the PR's touched lines). `pr_hint`/`main_hint` carry the
  short changed text for provenance (e.g. `MRDT 3%` vs `MRDT 2%`).
- Cached as `reconcile.json` in the run dir.

## 4. How it's consumed

- **Manifest / AC derivation (skill Phase 1):** the manifest is built from the **main
  snapshot** of the touched files (the PR is only the change-locator). `qa_targets` passes
  the reconciled context (main snapshot + divergence list) into the skill, and the three
  SKILL.md files / template instruct Phase 1 to derive ACs/expected values from main, not
  the raw PR diff. So "expected MRDT" = main's 2%.
- **API smoke (sub-project 3):** expected values read from the same main snapshot.
- **Divergence guard (scoring):** each divergence that maps to a ticket value produces (or
  annotates) an **AC-tied** TC with `status: needs-review`/`fail` and a note
  `"AC superseded by main: PR=<pr_hint>, main=<main_hint>"`. Because it is AC-tied, the
  scoring policy counts it — a run **cannot PASS** while asserting a value main has changed.

## 5. Flow

```
qa_targets → resolved PR refs
      │
      ▼
qa_reconcile.reconcile  ── gh API ──► main-HEAD + PR-head blobs ─► diff touched regions
      │  main_snapshot + divergences (reconcile.json)
      ├──► skill Phase 1 builds manifest/ACs from MAIN snapshot
      ├──► API smoke reads expected values from MAIN snapshot
      └──► divergence guard → AC-tied TC(s) → scoring (cannot pass stale value)
      │
      ▼
agent run → summary.json → canonical score (scoring-policy spec)
```

## 6. Error handling

- gh unauthenticated / repo or PR not found / API failure → `status:"degraded"`,
  `degraded_reason` set. The run continues PR-only BUT emits a visible **scoring** TC
  `TC-RECON` with `status: needs-review` and note "main reconciliation unavailable — values
  not verified against main", so a degraded run **never silently passes** on PR-only values.
- No PRs resolved for the ticket → skip reconciliation (nothing to anchor), log once.
- A divergence with no clear value mapping → still listed in the report's divergence section
  (advisory note) even if it doesn't map to a specific AC TC.

## 7. Testing

- `reconcile()` with mocked gh: PR-head vs main-head identical → no divergence; main-head
  changed a PR-touched line → one divergence with `pr_hint`/`main_hint`; multi-repo
  (services + ux); degraded path on gh error → `status:"degraded"` + reason.
- Divergence guard: a divergence mapped to a ticket value → AC-tied `needs-review`/`fail`
  TC; scoring policy then prevents PASS.
- Orchestrator: reconciled context (main snapshot) reaches the manifest step; degraded run
  emits `TC-RECON` and does not silently pass.

## 8. Files

| File | Change |
|---|---|
| `backend/qa_reconcile.py` | NEW — gh-API main/PR snapshots + divergence diff |
| `backend/qa_targets.py` | resolve PR refs → pass reconciled context (main snapshot + divergences) |
| `backend/qa_orchestrator.py` | run `reconcile()` pre-step; surface `TC-RECON`/divergence TCs |
| live skills + `backend/templates/qa-evidence.skill.base.md` | Phase 1: build ACs/expected values from the MAIN snapshot, not the raw PR diff |
| `backend/tests/test_qa_reconcile.py`, `test_qa_targets.py`, `test_qa_orchestrator.py` | NEW / extended |

## 9. Open risks

- **Value mapping**: linking a textual divergence (e.g. `MRDT 3%`→`2%`) to the specific AC
  TC is heuristic; when uncertain, surface it as a divergence note + a `needs-review`
  AC-tied TC rather than guess a hard fail. Unit tests pin the mapping on the MRDT case.
- **gh rate limits / large diffs**: fetch only PR-touched files (not whole trees); cache per
  run; degrade gracefully.
- **Pre-merge PRs** (qa-feature): main-HEAD may not yet contain the PR — divergence is then
  "main moved under the PR's base"; same diff logic applies (PR-head vs current main-head).

## 10. Related
- Depends on `2026-06-29-qa-scoring-policy-design.md` (divergence → AC-tied scoring TC).
- Feeds `2026-06-29-gated-api-smoke-design.md` (expected values from main snapshot).
- Memory: `xinventory-git-account`, `beeventory-qa-evidence-INV-588` (check CURRENT main),
  `xinventory-api-postman`.
