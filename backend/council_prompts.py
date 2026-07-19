"""Reviewer prompts for the Council Review Gate.

Kept in its own module so prompt tuning is a single-file diff that doesn't
touch orchestration code.
"""
from __future__ import annotations

import re

try:
    from qa_patterns import classify, PatternMatch
except Exception:  # pragma: no cover — patterns is optional
    classify = None  # type: ignore
    PatternMatch = None  # type: ignore

_MAX_DIFF_CHARS = 80_000


def _extract_changed_files(diff_text: str) -> list[str]:
    """Pull the b-side paths out of a unified diff. 'diff --git a/X b/Y' -> Y."""
    paths: list[str] = []
    for m in re.finditer(r"^diff --git a/\S+ b/(\S+)$", diff_text, re.MULTILINE):
        paths.append(m.group(1))
    return paths


def _pattern_audit_block(
    ticket_key: str,
    ticket_text: str,
    diffs: dict[str, str],
) -> str:
    """Render the pattern-audit section for the code reviewer prompt.

    Empty string if the classifier is unavailable or no patterns match —
    reviewer prompt stays unchanged in that case.
    """
    if classify is None:
        return ""
    all_files: list[str] = []
    combined_diff = ""
    for diff in diffs.values():
        all_files.extend(_extract_changed_files(diff))
        combined_diff += "\n" + diff
    matches = classify(
        changed_files=all_files,
        diff_text=combined_diff,
        ticket_text=ticket_text or "",
    )
    if not matches:
        return ""
    lines = [
        "",
        "QA BUG-PATTERN AUDIT",
        "-" * 60,
        (
            "The diff above touches surfaces that match historical QA-miss "
            "categories from /docs/superpowers/reports/2026-06-09-qa-bug-pattern-analysis.md "
            f"(sample: 274 bugs). Patterns that apply to ticket {ticket_key}:"
        ),
        "",
    ]
    for m in matches:
        lines.append(f"  - {m.id}: {m.name} — {m.why}")
        lines.append(f"      matched on: {', '.join(m.matched_on[:3])}"
                     + (" ..." if len(m.matched_on) > 3 else ""))
    lines.extend(
        [
            "",
            "For EACH pattern above, look at the PR diff (and the test files "
            "in the diff, if any) and check:",
            "  - Does the PR description / linked test plan explicitly address "
            "the typical bug shape for this pattern?",
            "  - Are there code-level tests covering it (e.g. for P3 block "
            "reorder: a test that inserts ≥3 blocks then reloads)?",
            "  - If the answer is NO for any high-severity pattern (P1, P3, "
            "P4, P6, P7, P8, P9, P11), that is a BLOCK reason — write it "
            "into the verdict line.",
            "",
            "Do NOT block solely because pattern TCs aren't authored — the "
            "qa-evidence skill injects those at QA time. Block only when the "
            "CODE itself is missing the unit/integration coverage that the "
            "pattern demands (e.g. PR changes lock semantics but has no "
            "concurrency test).",
        ]
    )
    return "\n" + "\n".join(lines)

_ADVERSARIAL_PREAMBLE = (
    "You are an ADVERSARIAL reviewer on a QA council. Your job is to find what is "
    "WRONG, not to approve. Do not be agreeable and do not give the benefit of the "
    "doubt. Assume the agent may have over-claimed, cut corners, or skipped edge "
    "cases, and actively hunt for it. A PASS requires positive, specific evidence "
    "that the work is correct AND complete — the mere absence of obvious problems is "
    "NOT enough. When evidence is missing, ambiguous, inconsistent, or you are "
    "uncertain, default to BLOCK. Approving weak work is a failure of your job.\n\n"
)

_VERDICT_FOOTER = (
    "\n\n---\n"
    "When you are done, output your verdict on the LAST LINE of your reply as "
    "EXACTLY one of:\n"
    "  VERDICT: PASS\n"
    "  VERDICT: BLOCK <one-line reason>\n"
    "Do not output any text after the verdict line. The label is case-sensitive."
)


def build_qa_evidence_prompt(ticket_key: str, run_name: str) -> str:
    run_path = f"~/evidence/{ticket_key}/runs/{run_name}"
    manifest_path = f"~/evidence/{ticket_key}/manifest.yml"
    return (
        _ADVERSARIAL_PREAMBLE
        + f"You are the QA-EVIDENCE reviewer for ticket {ticket_key}. Using ONLY file "
        "reads (no shell commands), you must interrogate the evidence run and decide "
        "whether it genuinely proves the ticket's acceptance criteria — not merely that "
        "files exist. A run that 'looks complete' but has TCs asserted without matching "
        "evidence is exactly what you must catch.\n\n"
        f"READ:\n"
        f"  - Manifest (acceptance criteria + planned test cases): {manifest_path}\n"
        f"  - Run summary + per-TC results: {run_path}/summary.json\n"
        f"  - Evidence files under: {run_path}/  (screenshots, automated/TC-*/, "
        "manual/, api-*.json, reconcile.json, notes)\n\n"
        "WORK THROUGH ALL OF THESE — any single failure is a BLOCK:\n\n"
        "1. AC COVERAGE. List every acceptance criterion from the manifest. Every AC "
        "MUST map to at least one test case in summary.json. Any AC with no covering TC "
        "is a coverage gap → BLOCK.\n\n"
        "2. EVERY TEST CASE vs ITS EVIDENCE. For EACH entry in summary.json test_cases, "
        "do NOT trust the status field — verify it against the actual evidence on disk:\n"
        "   - A `pass` MUST be backed by concrete, matching evidence: a screenshot that "
        "actually shows the asserted state, an `api-*.json` whose status/body matches the "
        "claim, or a manual note with a real observation. A `pass` on missing, empty, "
        "zero-byte, placeholder, or error-page evidence is a BLOCK.\n"
        "   - The evidence file the TC cites must EXIST and be non-empty. A TC that cites "
        "an endpoint must have its `api-*.json`; a TC with a user-visible surface must "
        "have a real screenshot.\n"
        "   - The evidence must actually depict the asserted behavior — not a login page, "
        "a blank/error overlay, the wrong route, or an unrelated view.\n\n"
        "3. INTERROGATE EVERY NON-PASS TC. For each TC whose status is `unknown`, "
        "`blocked`, `needs-review`, or `fail`: is it a legitimate, explained limitation, "
        "or a hidden gap being glossed over? An `unknown`/`blocked` TC on an in-scope AC "
        "with no justification is a BLOCK, not a shrug.\n\n"
        "4. SCORE / VERDICT INTEGRITY. The headline score+verdict must be consistent "
        "with the SCORED test cases (AC-tied TCs + `TC-UV-1` console + `TC-UV-2` "
        "network). A `PASS`/high score while a scored TC failed, or a score that doesn't "
        "match the TC tally, is a BLOCK.\n\n"
        "5. MAIN RECONCILIATION. If `reconcile.json` or any `TC-RECON` entry is present, "
        "current main is authoritative: verify the run asserted MAIN's values, not the "
        "PR's stale values. An unaddressed divergence (a TC that passed on the old value) "
        "is a BLOCK.\n\n"
        "6. API SMOKE. If the ticket touches the API, `TC-API-*` entries must be present "
        "and their `api-*.json` evidence real. A 5xx / timeout API result must be visibly "
        "surfaced (it is advisory for the headline, but must not be silently dropped).\n\n"
        "7. STALE-EVIDENCE TRAP. Be alert for screenshots of a cached/old bundle, a stale "
        "asset hash, or the wrong URL masquerading as a successful verification.\n\n"
        "If everything above holds with positive, specific, matching evidence, output "
        "PASS. If ANYTHING is missing, empty, inconsistent, unverifiable, or merely "
        "asserted without proof, output BLOCK with the single most important reason."
        + _VERDICT_FOOTER
    )


def build_code_reviewer_prompt(
    ticket_key: str,
    pr_refs: list[dict],
    diffs: dict[str, str],
    ticket_text: str = "",
) -> str:
    if not pr_refs:
        return (
            f"You are the CODE-REVIEWER for ticket {ticket_key}.\n\n"
            "There are no pull requests linked to this ticket. There is "
            "nothing to review. Output VERDICT: PASS."
            + _VERDICT_FOOTER
        )

    lines = [
        f"You are the CODE-REVIEWER for ticket {ticket_key}.",
        "",
        "Review the following pull request diffs. Flag only:",
        "  - Correctness bugs (logic errors, off-by-one, missing nil/null checks)",
        "  - Missing tests for new behavior",
        "  - Obvious security issues (secrets, injection, missing auth)",
        "",
        "Do NOT flag style, naming, or formatting.",
        "",
        "If you find one or more blocking issues, output BLOCK with a one-line "
        "summary of the most important issue. Otherwise output PASS.",
        "",
        "=" * 60,
    ]
    for pr in pr_refs:
        repo = pr.get("repo", "?")
        pr_id = pr.get("pr_id", "?")
        title = pr.get("title", "")
        key = f"{repo}/{pr_id}"
        diff = diffs.get(key, "(diff unavailable)")
        lines.append(f"\nPR: {repo} #{pr_id} — {title}\n")
        lines.append("```diff")
        # Truncate enormous diffs so we don't blow the context window.
        # _MAX_DIFF_CHARS is a soft cap that fits comfortably below Claude's input limit.
        if len(diff) > _MAX_DIFF_CHARS:
            lines.append(diff[:_MAX_DIFF_CHARS])
            lines.append(f"\n... (diff truncated, original was {len(diff)} chars)")
        else:
            lines.append(diff)
        lines.append("```")

    audit = _pattern_audit_block(ticket_key, ticket_text, diffs)
    return _ADVERSARIAL_PREAMBLE + "\n".join(lines) + audit + _VERDICT_FOOTER
