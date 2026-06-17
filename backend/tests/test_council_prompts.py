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
