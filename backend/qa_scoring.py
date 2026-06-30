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
