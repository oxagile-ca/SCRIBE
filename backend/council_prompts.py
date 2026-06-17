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
    return (
        f"You are the QA-EVIDENCE reviewer for ticket {ticket_key}.\n\n"
        f"Read the evidence run at: {run_path}\n\n"
        "Your job is to verify, using only file reads (no shell commands):\n"
        "  1. `summary.json` exists in the run directory.\n"
        "  2. `summary.json` has a numeric `score` field.\n"
        "  3. `automated/` contains at least one Playwright artifact "
        "(e.g. `.spec.ts` results, `trace.zip`, or screenshots).\n"
        "  4. The score is consistent with the artifacts: a passing score "
        "must not coexist with test logs that show failures.\n\n"
        "ADDITIONAL BLOCK RULES — apply only when the PR/diff context indicates "
        "the surface is in scope. If a rule's precondition is met and the "
        "required evidence is absent from the run directory (check `manual/`, "
        "`automated/`, screenshots, and any `*.md` / `notes.md` / "
        "`manual-note*` files), output BLOCK:\n"
        "  5. FOCUS RETENTION — If the PR touches a list-builder / tag / chip "
        "/ multi-add / autocomplete input (e.g. files matching "
        "ImageMetadataForm*, TagInput*, ChipInput*, MultiAdd*, Autocomplete*, "
        "or diff/ticket mentions v-autocomplete, refreshTags, `:key=\"refresh`, "
        "addTag, chip): BLOCK if there is no evidence of focus-retention "
        "verification — no `document.activeElement` assertion in a manual "
        "note AND no multi-add screenshot (3+ items added in a row without "
        "re-clicking the field).\n"
        "  6. SAVE → PUBLISH PREVIEW — If the PR is a save-able form (touches "
        "save/publish/serializer/schema, or the ticket describes a doc-edit "
        "AC): BLOCK if there is no Save → Publish preview evidence (a "
        "screenshot or note showing Preview opened after Save and rendered "
        "without console errors).\n"
        "  7. MULTI-TEMPLATE — If the PR is template-driven (touches "
        "templates, validators, schemas, or the ticket mentions Article / "
        "Recipe / Spotlight / BIO / FAQ / Quilt / ListSC / SC): BLOCK if "
        "only one template/variation was tested — evidence must cover at "
        "least 2 templates or variations.\n"
        "  8. KEYBOARD ENTRY — If the PR has keyboard-reachable inputs "
        "(text fields, autocomplete, chip/tag inputs, pickers, modals with "
        "form controls): BLOCK if there is no keyboard-entry path evidence "
        "(Enter / Tab / Shift-Tab / Esc exercised where applicable).\n\n"
        "If any check fails, output BLOCK with a one-line reason. If all "
        "checks pass, output PASS."
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
    return "\n".join(lines) + audit + _VERDICT_FOOTER
