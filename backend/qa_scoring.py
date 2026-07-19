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
_API_RE = re.compile(r"^TC-API\b")
FAIL_PCT = 60  # a scoring fail with pass-rate below this is a hard FAIL


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
