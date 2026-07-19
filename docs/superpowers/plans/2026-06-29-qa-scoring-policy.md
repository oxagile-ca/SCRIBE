# QA Scoring Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the canonical QA score/verdict deterministically in the backend from the AC-tied test cases plus the console (TC-UV-1) and network (TC-UV-2) scans only; make all other scans advisory (never lower the headline); the backend score overrides the agent's self-reported number.

**Architecture:** A new pure module `qa_scoring.py` classifies test-case ids and tallies a canonical score over the scoring set. `qa_orchestrator.run_and_finalize` recomputes and overwrites `summary.json`'s `score`/`verdict` right after the run produces it (before report/attach), so every downstream consumer (`generate_html_report`, `read_run_summary`, `check_evidence`) reads the canonical values. `generate_html_report` renders a "Scored" vs "Advisory (not scored)" split.

**Tech Stack:** Python 3.12 (project venv), pytest. No new dependencies.

## Global Constraints

- **Test runner:** `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest` (default `python` is 3.14 without pytest). Run all commands from `C:/Users/ankit/SCRIBE/backend`.
- **Scoring set (counts toward score/verdict):** AC-tied `TC-<KEY>-NNN` (any id that is not a UV/API id) **plus** `TC-UV-1` (console) and `TC-UV-2` (network).
- **Advisory set (reported, never scores, never penalized when skipped/incomplete):** `TC-API-*`, `TC-UV-3`, `TC-UV-4`, `TC-UV-5`, `TC-UV-6`.
- **Fail-safe:** an unknown TC id classifies as **scoring** (never silently drop a real AC TC).
- **Backend authority:** the computed score OVERWRITES `summary.json` `score`/`verdict`.
- **Verdict mapping:** `FAIL` if any scoring fail and `pct < 60`; else `PASS-WITH-ISSUES` if any scoring fail; else `BLOCKED` if any scoring blocked; else `PASS-WITH-ISSUES` if any scoring needs-review; else `PASS`; `BLOCKED` if the scoring denominator is 0. `exempt`/`skipped` scoring TCs are excluded from the denominator.
- **Commit style:** end commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on branch `feat/headless-qa-phase2`.

---

### Task 1: `classify_tc` — scoring vs advisory

**Files:**
- Create: `backend/qa_scoring.py`
- Test: `backend/tests/test_qa_scoring.py`

**Interfaces:**
- Produces: `classify_tc(tc_id: str) -> "scoring" | "advisory"`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_qa_scoring.py
import qa_scoring


class TestClassifyTc:
    def test_ac_tied_id_is_scoring(self):
        assert qa_scoring.classify_tc("TC-0675-001") == "scoring"

    def test_console_and_network_uv_are_scoring(self):
        assert qa_scoring.classify_tc("TC-UV-1") == "scoring"
        assert qa_scoring.classify_tc("TC-UV-2") == "scoring"

    def test_other_uv_are_advisory(self):
        for tc in ("TC-UV-3", "TC-UV-4", "TC-UV-5", "TC-UV-6"):
            assert qa_scoring.classify_tc(tc) == "advisory"

    def test_api_is_advisory(self):
        assert qa_scoring.classify_tc("TC-API-user-1") == "advisory"
        assert qa_scoring.classify_tc("tc-api-fees-3") == "advisory"

    def test_unknown_id_defaults_to_scoring(self):
        assert qa_scoring.classify_tc("WEIRD") == "scoring"
        assert qa_scoring.classify_tc("") == "scoring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'qa_scoring'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/qa_scoring.py
"""Deterministic QA scoring policy.

The headline score/verdict are computed from the SCORING set of test cases only —
the ticket's acceptance-criteria TCs plus the console (TC-UV-1) and network (TC-UV-2)
scans. Everything else is advisory: API smoke (TC-API-*), accessibility (TC-UV-5),
assets (TC-UV-3), doc-lifecycle (TC-UV-4) and snapshot drift (TC-UV-6) are reported but
never lower the headline, and being skipped/incomplete never penalizes.

Computed in the backend; OVERRIDES the agent's self-reported score so the policy holds
regardless of what the qa-evidence skill wrote into summary.json.
"""
import re

ADVISORY_UV = {"TC-UV-3", "TC-UV-4", "TC-UV-5", "TC-UV-6"}
_API_RE = re.compile(r"^TC-API\b", re.IGNORECASE)


def classify_tc(tc_id: str) -> str:
    """'scoring' or 'advisory' for a test-case id.

    Scoring: AC-tied TCs (anything not UV/API) plus TC-UV-1 (console) and TC-UV-2
    (network). Advisory: TC-UV-3/4/5/6 and TC-API-*. Unknown ids → 'scoring' (fail-safe).
    """
    tc = (tc_id or "").strip().upper()
    if _API_RE.match(tc):
        return "advisory"
    if tc in ADVISORY_UV:
        return "advisory"
    return "scoring"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_scoring.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/qa_scoring.py backend/tests/test_qa_scoring.py
git commit -m "feat(qa_scoring): classify_tc scoring vs advisory

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `compute_score` + `split_test_cases`

**Files:**
- Modify: `backend/qa_scoring.py`
- Test: `backend/tests/test_qa_scoring.py`

**Interfaces:**
- Consumes: `classify_tc` (Task 1)
- Produces:
  - `compute_score(test_cases: list[dict]) -> dict` with keys `pass, fail, blocked, needs_review, total, pct, verdict, scoring_ids, advisory_ids`
  - `split_test_cases(test_cases: list[dict]) -> tuple[list, list]` → `(scored, advisory)` preserving order

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_qa_scoring.py
def _tc(tc_id, status):
    return {"id": tc_id, "status": status}


class TestComputeScore:
    def test_advisory_failures_do_not_lower_score(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-0675-002", "pass"),
               _tc("TC-UV-1", "pass"), _tc("TC-UV-2", "pass"),
               _tc("TC-UV-5", "fail"), _tc("TC-API-user-1", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["pct"] == 100
        assert s["verdict"] == "PASS"
        assert s["total"] == 4  # advisory excluded from denominator

    def test_scoring_console_fail_downgrades(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-UV-1", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["fail"] == 1
        assert s["pct"] == 50
        assert s["verdict"] == "FAIL"  # 50 < 60

    def test_scoring_fail_high_passrate_is_pass_with_issues(self):
        tcs = [_tc(f"TC-X-00{i}", "pass") for i in range(1, 9)] + [_tc("TC-X-009", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["verdict"] == "PASS-WITH-ISSUES"  # 8/9 ≈ 89 ≥ 60

    def test_needs_review_blocks_clean_pass(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-RECON", "needs-review")]
        s = qa_scoring.compute_score(tcs)
        assert s["verdict"] == "PASS-WITH-ISSUES"

    def test_exempt_and_skipped_excluded_from_denominator(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-UV-4", "exempt"),
               _tc("TC-X-002", "skipped")]
        s = qa_scoring.compute_score(tcs)
        assert s["total"] == 1 and s["pct"] == 100 and s["verdict"] == "PASS"

    def test_empty_is_blocked(self):
        s = qa_scoring.compute_score([])
        assert s["total"] == 0 and s["verdict"] == "BLOCKED"

    def test_scoring_blocked_is_blocked(self):
        s = qa_scoring.compute_score([_tc("TC-0675-001", "blocked")])
        assert s["verdict"] == "BLOCKED"

    def test_split_preserves_order(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-API-1", "pass"), _tc("TC-UV-2", "pass")]
        scored, advisory = qa_scoring.split_test_cases(tcs)
        assert [t["id"] for t in scored] == ["TC-0675-001", "TC-UV-2"]
        assert [t["id"] for t in advisory] == ["TC-API-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_scoring.py -q`
Expected: FAIL — `AttributeError: module 'qa_scoring' has no attribute 'compute_score'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to backend/qa_scoring.py
FAIL_PCT = 60  # a scoring fail with pass-rate below this is a hard FAIL


def _tc_id(tc):
    return tc.get("id") or tc.get("tc") or ""


def split_test_cases(test_cases):
    """(scored, advisory) lists, order preserved."""
    scored, advisory = [], []
    for tc in test_cases or []:
        (advisory if classify_tc(_tc_id(tc)) == "advisory" else scored).append(tc)
    return scored, advisory


def compute_score(test_cases):
    """Canonical score/verdict over the SCORING test cases only.

    Returns pass/fail/blocked/needs_review/total/pct/verdict + scoring_ids/advisory_ids.
    pct = pass-rate over scoring TCs (advisory excluded numerator AND denominator;
    exempt/skipped scoring TCs excluded from the denominator).
    """
    scoring_ids, advisory_ids = [], []
    p = f = b = nr = 0
    for tc in test_cases or []:
        tc_id = _tc_id(tc)
        status = (tc.get("status") or "").strip().lower()
        if classify_tc(tc_id) == "advisory":
            advisory_ids.append(tc_id)
            continue
        scoring_ids.append(tc_id)
        if status in ("exempt", "skipped", "n/a", ""):
            continue
        if status == "pass":
            p += 1
        elif status == "fail":
            f += 1
        elif status == "blocked":
            b += 1
        elif status == "needs-review":
            nr += 1
        else:
            p += 1  # unknown but present status → soft pass (don't penalize)
    total = p + f + b + nr
    pct = round(100 * p / total) if total else 0
    if total == 0:
        verdict = "BLOCKED"
    elif f > 0:
        verdict = "FAIL" if pct < FAIL_PCT else "PASS-WITH-ISSUES"
    elif b > 0:
        verdict = "BLOCKED"
    elif nr > 0:
        verdict = "PASS-WITH-ISSUES"
    else:
        verdict = "PASS"
    return {"pass": p, "fail": f, "blocked": b, "needs_review": nr,
            "total": total, "pct": pct, "verdict": verdict,
            "scoring_ids": scoring_ids, "advisory_ids": advisory_ids}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_scoring.py -q`
Expected: PASS (all qa_scoring tests)

- [ ] **Step 5: Commit**

```bash
git add backend/qa_scoring.py backend/tests/test_qa_scoring.py
git commit -m "feat(qa_scoring): compute_score + split_test_cases (advisory excluded)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Recompute + overwrite `summary.json` in `run_and_finalize`

**Files:**
- Modify: `backend/qa_orchestrator.py` (after the `summary.json` existence check, before `generate_html_report` — currently lines 99→101)
- Test: `backend/tests/test_qa_orchestrator.py`

**Interfaces:**
- Consumes: `qa_scoring.compute_score` (Task 2)
- Produces: on finalize, `summary.json` has canonical `score` (`{pass,fail,blocked,total,pct}`), `verdict`, and `scoring` (`{scoring_ids, advisory_ids}`).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_qa_orchestrator.py
import json
import os
import qa_orchestrator


def test_finalize_overwrites_summary_with_canonical_score(monkeypatch, tmp_path):
    run_dir = tmp_path / "INV-700" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    # Agent wrote a WRONG score (90) dragged down by an advisory AXE fail; AC TCs all pass.
    (run_dir / "summary.json").write_text(json.dumps({
        "ticket": "INV-700", "verdict": "PASS-WITH-ISSUES",
        "score": {"pass": 2, "fail": 1, "total": 3, "pct": 67},
        "test_cases": [
            {"id": "TC-700-001", "status": "pass"},
            {"id": "TC-UV-1", "status": "pass"},
            {"id": "TC-UV-5", "status": "fail"},
            {"id": "TC-API-1", "status": "fail"},
        ],
    }), encoding="utf-8")

    async def fake_qa_run(*a, **k):
        yield {"type": "qa_complete", "success": True, "run_name": "run-1", "error": None}
    monkeypatch.setattr(qa_orchestrator.qa_runner, "run", fake_qa_run)
    monkeypatch.setattr(qa_orchestrator, "EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(qa_orchestrator, "generate_html_report",
                        lambda k, r: (True, "ok", f"/evidence/{k}/runs/{r}/index.html"))
    async def fake_pdf(html, **kw): return None
    monkeypatch.setattr(qa_orchestrator.pdf_export, "export", fake_pdf)
    monkeypatch.setattr(qa_orchestrator, "load_instance_config", lambda: {})
    monkeypatch.setattr(qa_orchestrator, "compute_attach_gate", lambda *a, **k: False)

    async def drain():
        async for _ in qa_orchestrator.run_and_finalize("INV-700", "http://x", armed=False):
            pass
    import asyncio
    asyncio.run(drain())

    out = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert out["score"]["pct"] == 100      # advisory fails excluded
    assert out["score"]["total"] == 2      # TC-700-001 + TC-UV-1
    assert out["verdict"] == "PASS"
    assert out["scoring"]["advisory_ids"] == ["TC-UV-5", "TC-API-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_orchestrator.py::test_finalize_overwrites_summary_with_canonical_score -q`
Expected: FAIL — `out["score"]["pct"]` is 67 (agent value, not overwritten)

- [ ] **Step 3: Write minimal implementation**

In `backend/qa_orchestrator.py`, add the import near the top (with the other imports):

```python
import qa_scoring
```

Then insert this block immediately after the `summary.json` existence check (after current line 99, before `ok, msg, report_url = generate_html_report(...)`):

```python
    # Canonical score: deterministic, backend-authoritative. Overwrites the agent's
    # self-reported number so advisory scans (API smoke, AXE, etc.) can't move the
    # headline. See docs/superpowers/specs/2026-06-29-qa-scoring-policy-design.md.
    import json as _json
    with open(summary_path, encoding="utf-8") as _f:
        _summary = _json.load(_f)
    _canon = qa_scoring.compute_score(_summary.get("test_cases", []))
    _summary["score"] = {"pass": _canon["pass"], "fail": _canon["fail"],
                         "blocked": _canon["blocked"], "total": _canon["total"],
                         "pct": _canon["pct"]}
    _summary["verdict"] = _canon["verdict"]
    _summary["scoring"] = {"scoring_ids": _canon["scoring_ids"],
                           "advisory_ids": _canon["advisory_ids"]}
    with open(summary_path, "w", encoding="utf-8") as _f:
        _json.dump(_summary, _f, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_qa_orchestrator.py -q`
Expected: PASS (new test + existing orchestrator tests still green)

- [ ] **Step 5: Commit**

```bash
git add backend/qa_orchestrator.py backend/tests/test_qa_orchestrator.py
git commit -m "feat(qa): backend-authoritative canonical score on finalize

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Report renders Scored vs Advisory; headline from canonical score

**Files:**
- Modify: `backend/agents.py` (`generate_html_report` — the test-case rendering section)
- Test: `backend/tests/test_evidence_report.py`

**Interfaces:**
- Consumes: `qa_scoring.split_test_cases` (Task 2); `summary["score"]["pct"]` (Task 3)
- Produces: the report HTML groups advisory TCs under an "Advisory — not scored" heading; the headline number is `summary["score"]["pct"]`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_evidence_report.py
def test_report_splits_advisory_and_uses_canonical_headline(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path))
    run_dir = os.path.join(str(tmp_path), "INV-701", "runs", "run-1")
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        _json.dump({
            "ticket": "INV-701", "verdict": "PASS",
            "score": {"pass": 1, "fail": 0, "total": 1, "pct": 100},
            "test_cases": [
                {"id": "TC-701-001", "title": "AC one", "status": "pass"},
                {"id": "TC-API-user-1", "title": "GET user", "status": "fail"},
                {"id": "TC-UV-5", "title": "a11y", "status": "fail"},
            ],
        }, f)
    ok, msg, url = agents.generate_html_report("INV-701", "run-1")
    assert ok
    html = open(os.path.join(run_dir, "index.html"), encoding="utf-8").read()
    assert "Advisory" in html              # advisory section rendered
    assert "TC-API-user-1" in html         # advisory TC still shown
    # headline reflects canonical 100, not dragged down by the advisory fails
    assert ">100<" in html or "100/100" in html or "100%" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_evidence_report.py::test_report_splits_advisory_and_uses_canonical_headline -q`
Expected: FAIL — no "Advisory" heading in the current report.

- [ ] **Step 3: Write minimal implementation**

In `backend/agents.py`, at the top of `generate_html_report` add `import qa_scoring`. Locate the section that builds the Test-Case cards (search for the `tc_ids` / `_norm_tc` rendering loop, ~line 1515+). Wrap the per-TC card builder in a small function `_tc_card(tc)` if not already, then replace the single render loop with two groups using `qa_scoring.split_test_cases`:

```python
    import qa_scoring
    scored_tcs, advisory_tcs = qa_scoring.split_test_cases(
        [{"id": tc_id, **(tc_detail_map.get(tc_id) or {})} for tc_id in tc_ids]
    )

    def _render_group(title, tcs, *, advisory=False):
        if not tcs:
            return ""
        note = " <span style='font-size:11px;color:#94a3b8'>(not scored)</span>" if advisory else ""
        cards = "".join(_tc_card(tc) for tc in tcs)
        return f"<section class='section'><h2>{title}{note}</h2><div class='tc-grid'>{cards}</div></section>"

    tc_section_html = (
        _render_group("Test Case Evidence", scored_tcs)
        + _render_group("Advisory — not scored", advisory_tcs, advisory=True)
    )
```

Then ensure the headline confidence ring/number is driven by `summary.get("score", {}).get("pct", score)` (the canonical pct) rather than a recomputed agent value, and that `tc_section_html` is inserted where the old single TC loop's output was. (Read the surrounding builder to keep variable names consistent — `tc_ids`, `tc_detail_map`, and the existing card markup function.)

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest tests/test_evidence_report.py -q`
Expected: PASS (new test + existing report tests still green)

- [ ] **Step 5: Run the full backend suite for regressions**

Run: `C:/Users/ankit/SCRIBE/.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_github_client.py`
Expected: only the known pre-existing failures (7 chat/council/quartermaster WinError) — no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/agents.py backend/tests/test_evidence_report.py
git commit -m "feat(report): scored vs advisory split; headline from canonical score

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Skill doc — stop docking confidence for advisory scans

**Files:**
- Modify: `~/.claude/skills/qa-evidence-beeventory/SKILL.md`, `~/.claude/skills/qa-evidence-xinventory/SKILL.md`, `backend/templates/qa-evidence.skill.base.md` (Phase 2.6 / 2.7 / 7.5 confidence guidance)

**Interfaces:** none (documentation; enforcement is in `qa_scoring`).

- [ ] **Step 1: Add the scoring-policy note to each file's confidence/score phase**

In the confidence-score phase (Phase 7.5) and the Universal Validation Suite (Phase 2.6) of all three files, add:

> **Scoring policy (enforced by the backend `qa_scoring`):** the headline score is computed
> from AC-tied TCs plus `TC-UV-1` (console) and `TC-UV-2` (network) ONLY. `TC-API-*`,
> `TC-UV-3/4/5/6` (incl. AXE accessibility) are **advisory** — report them, but do NOT
> reduce confidence or verdict when they are skipped, incomplete, or failing. The backend
> overwrites `summary.json` score/verdict with the canonical value regardless.

- [ ] **Step 2: Verify no contradictory wording remains**

Run: `grep -niE "a11y|axe|accessibility|confidence" backend/templates/qa-evidence.skill.base.md`
Expected: any "reduce confidence for a11y/AXE" wording is removed or reconciled with the note above.

- [ ] **Step 3: Commit**

```bash
git add backend/templates/qa-evidence.skill.base.md
git commit -m "docs(qa-evidence): advisory scans do not dock confidence (scoring policy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** §2 policy → Tasks 1–2; §3 module → Tasks 1–2; §4 integration (orchestrator overwrite, report split, check_evidence reads canonical) → Tasks 3–4; §5 confidence → Task 5; §6 error handling (empty/unknown) → Task 2 tests; §7 testing → each task. Covered.
- **Placeholders:** none — all steps carry real code/commands.
- **Type consistency:** `compute_score` keys (`pass,fail,blocked,needs_review,total,pct,verdict,scoring_ids,advisory_ids`) used consistently in Tasks 2–4; `split_test_cases` returns `(scored, advisory)` used in Task 4; `classify_tc` strings `"scoring"|"advisory"` consistent.
- **Note for Task 4:** `generate_html_report` is large — the implementer must read the existing TC-render section to wire `_tc_card`/`tc_detail_map` names correctly; the test pins the observable outcome (Advisory heading + canonical headline) rather than internal structure.
