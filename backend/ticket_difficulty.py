"""Derived ticket difficulty (story points are unused on the board).

Mirrors the frontend `extractACs` heuristic: counts acceptance-criteria-style lines
(under an 'AC:'/'Acceptance Criteria' header, or bullet lines) and adds a small bump
for long descriptions, then buckets into Easy / Medium / Hard. Pure — no I/O.
"""
import re

_AC_PREFIX = re.compile(r"^ac[:\s]", re.I)
_AC_HEADER = re.compile(r"acceptance\s*criteria", re.I)
_BULLET = re.compile(r"^[\*\-]\s")
_BULLET_STRIP = re.compile(r"^[\*\-]\s*")


def count_acceptance_criteria(description: str) -> int:
    """Count AC-style lines. A header line ('AC:' / 'Acceptance Criteria') turns the AC
    section on (and is skipped); while in-section, or for any bullet line, the de-bulleted
    text counts when it is longer than 10 chars; a blank line ends the section."""
    if not description:
        return 0
    count = 0
    in_ac = False
    for raw in description.split("\n"):
        line = raw.strip()
        if _AC_PREFIX.match(line) or _AC_HEADER.search(line):
            in_ac = True
            continue
        if in_ac or _BULLET.match(line):
            clean = _BULLET_STRIP.sub("", line).strip()
            if len(clean) > 10:
                count += 1
        if in_ac and line == "":
            in_ac = False
    return count


def compute_difficulty(description: str) -> tuple[str, int]:
    """Return (label, score). score = AC count + length bump (+1 per 600 chars beyond the
    first 600, capped at +3). Buckets: <=2 Easy, 3..5 Medium, >=6 Hard."""
    description = description or ""
    ac = count_acceptance_criteria(description)
    length_bump = min(3, max(0, (len(description) - 600) // 600))
    score = ac + length_bump
    if score <= 2:
        label = "Easy"
    elif score <= 5:
        label = "Medium"
    else:
        label = "Hard"
    return label, score
