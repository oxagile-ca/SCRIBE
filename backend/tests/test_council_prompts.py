"""The council reviewers must be adversarial — actively critical, defaulting to BLOCK
when evidence is missing/uncertain, not rubber-stamping the agent's work."""
from council_prompts import build_qa_evidence_prompt, build_code_reviewer_prompt


def _is_adversarial(text: str) -> bool:
    low = text.lower()
    skeptical = any(w in low for w in ("adversarial", "skeptic", "do not be agreeable", "not to approve"))
    defaults_block = any(w in low for w in ("default to block", "when in doubt", "uncertain", "ambiguous"))
    return skeptical and defaults_block


def test_qa_evidence_prompt_is_adversarial():
    p = build_qa_evidence_prompt("INV-1", "run-qa-1")
    assert _is_adversarial(p)
    assert "VERDICT: PASS" in p and "VERDICT: BLOCK" in p


def test_qa_evidence_prompt_interrogates_every_ac_and_test_case():
    """The reviewer must go past file-existence and actually challenge each AC/TC against
    the evidence — otherwise it rubber-stamps (the loose-ends problem)."""
    low = build_qa_evidence_prompt("INV-1", "run-qa-1").lower()
    # reads ACs from the manifest and checks every AC is covered by a TC
    assert "manifest" in low
    assert "acceptance" in low or "acs" in low
    assert "coverage" in low or "every ac" in low or "each ac" in low
    # enumerates EVERY test case, not just "an artifact exists"
    assert "every test case" in low or "each test case" in low
    # each TC's status must be backed by concrete, matching evidence
    assert "evidence" in low and "status" in low
    assert "screenshot" in low
    # interrogate non-pass statuses instead of glossing them
    assert "unknown" in low and ("needs-review" in low or "blocked" in low)
    # score/verdict must be consistent with the scored TCs
    assert "score" in low and "verdict" in low
    # the recurring stale/cached-evidence trap
    assert "stale" in low or "cached" in low
    # reconciliation (spec #2) and API smoke (spec #3) evidence must be checked
    assert "recon" in low          # reconcile / TC-RECON
    assert "tc-api" in low or "api smoke" in low


def test_qa_evidence_prompt_no_dotdash_specific_rules():
    """The old Dotdash-only rules (Article/Recipe templates, ImageMetadataForm) don't apply
    to this instance and were noise — they should be gone."""
    low = build_qa_evidence_prompt("INV-1", "run-qa-1").lower()
    assert "imagemetadataform" not in low
    assert "recipe" not in low and "spotlight" not in low


def test_code_reviewer_prompt_is_adversarial():
    p = build_code_reviewer_prompt(
        "INV-1",
        [{"repo": "acme/cms", "pr_id": "1", "title": "Add SKU search"}],
        {"acme/cms/1": "diff --git a/x.py b/x.py\n+code"},
    )
    assert _is_adversarial(p)


def test_code_reviewer_with_no_prs_still_passes():
    # Nothing to review is the one legitimate auto-PASS.
    p = build_code_reviewer_prompt("INV-1", [], {})
    assert "VERDICT: PASS" in p
