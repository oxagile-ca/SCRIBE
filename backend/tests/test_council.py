import json as _json
import os

import pytest

from council import _parse_verdict_line, _synthesize, append_audit, COUNCIL_AUDIT_PATH, _run_reviewer, Reviewer
from council_prompts import build_qa_evidence_prompt, build_code_reviewer_prompt


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _set_stub(monkeypatch, name):
    path = os.path.join(FIXTURES_DIR, f"stub_claude_{name}.sh")
    monkeypatch.setenv("CLAUDE_BIN", path)


def test_build_reviewer_cmd_is_argv_with_prompt_intact():
    # Regression: the reviewer command must be an argv list (consumed by
    # create_subprocess_exec), NOT a shell-quoted string. The old shell+shlex.quote
    # build mangled the prompt on Windows (cmd.exe ignores POSIX single quotes), so
    # claude received a stray `'You` instead of the prompt and never returned a
    # VERDICT -> council BLOCKed every ticket.
    from council import _build_reviewer_cmd
    prompt = "You are the QA reviewer.\nEnd with exactly: VERDICT: PASS or BLOCK."
    cmd = _build_reviewer_cmd(prompt)
    assert isinstance(cmd, list)
    assert cmd[-1] == prompt  # whole prompt arrives as ONE argument, unsplit
    assert "-p" in cmd


def test_parse_simple_pass():
    text = "Looks good overall.\nVERDICT: PASS"
    assert _parse_verdict_line(text) == ("PASS", "")


def test_parse_simple_block_with_reason():
    text = "Found issues.\nVERDICT: BLOCK missing tests for new endpoint"
    assert _parse_verdict_line(text) == ("BLOCK", "missing tests for new endpoint")


def test_parse_returns_last_verdict_when_multiple():
    text = "VERDICT: PASS\nactually wait\nVERDICT: BLOCK changed my mind"
    assert _parse_verdict_line(text) == ("BLOCK", "changed my mind")


def test_parse_verdict_not_on_last_line():
    text = "VERDICT: PASS\nFinal thoughts: everything looks fine."
    assert _parse_verdict_line(text) == ("PASS", "")


def test_parse_no_verdict_returns_none():
    text = "I forgot to write a verdict line."
    assert _parse_verdict_line(text) == (None, "")


def test_parse_block_no_reason():
    text = "VERDICT: BLOCK"
    assert _parse_verdict_line(text) == ("BLOCK", "")


def test_parse_case_sensitive_verdict_label():
    text = "verdict: PASS"
    assert _parse_verdict_line(text) == (None, "")


def test_parse_whitespace_in_reason():
    text = "VERDICT: BLOCK    extra spaces stripped   "
    assert _parse_verdict_line(text) == ("BLOCK", "extra spaces stripped")


def test_parse_crlf_line_endings():
    text = "VERDICT: PASS\r\n"
    assert _parse_verdict_line(text) == ("PASS", "")


def test_parse_reason_does_not_cross_newline():
    # Locks in why the regex uses [ \t] rather than \s — a "simplification"
    # back to \s+ would silently regress this case.
    text = "VERDICT: BLOCK\nfollowing line is not the reason"
    assert _parse_verdict_line(text) == ("BLOCK", "")


def _ro(name, verdict, reason="", stdout="", error=None):
    """Build a ReviewerOutcome dict for the synthesizer."""
    return {
        "name": name,
        "verdict": verdict,    # "PASS" | "BLOCK" | None (unparseable) | "ERROR"
        "reason": reason,
        "stdout": stdout,
        "error": error,
    }


def test_synthesize_all_pass():
    result = _synthesize([
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", "PASS"),
    ])
    assert result["verdict"] == "PASS"
    assert result["rationale"] == "All reviewers passed."
    assert len(result["reviewers"]) == 2


def test_synthesize_one_block():
    result = _synthesize([
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", "BLOCK", reason="missing tests for X"),
    ])
    assert result["verdict"] == "BLOCK"
    assert "code-reviewer: missing tests for X" in result["rationale"]


def test_synthesize_missing_verdict_line():
    result = _synthesize([
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", None, stdout="(long transcript)"),
    ])
    assert result["verdict"] == "BLOCK"
    assert "did not return a verdict" in result["rationale"]


def test_synthesize_reviewer_error():
    result = _synthesize([
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", "ERROR", error="reviewer crashed: exit 1"),
    ])
    assert result["verdict"] == "BLOCK"
    assert "code-reviewer: reviewer crashed" in result["rationale"]


def test_synthesize_preserves_per_reviewer_detail():
    outcomes = [
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", "BLOCK", reason="oops"),
    ]
    result = _synthesize(outcomes)
    names = {r["name"] for r in result["reviewers"]}
    assert names == {"qa-evidence", "code-reviewer"}


def test_synthesize_empty_outcomes_blocks():
    # Hard gate: no reviewers means no evidence of approval. Fail closed.
    result = _synthesize([])
    assert result["verdict"] == "BLOCK"
    assert result["rationale"] == "no reviewers ran"
    assert result["reviewers"] == []


def test_synthesize_multiple_blocks_joined_with_semicolon():
    # Pins the "; " separator so a regression to ", " or "\n" is caught.
    result = _synthesize([
        _ro("qa-evidence", "BLOCK", reason="evidence missing"),
        _ro("code-reviewer", "BLOCK", reason="no tests"),
    ])
    assert result["verdict"] == "BLOCK"
    assert result["rationale"] == "qa-evidence: evidence missing; code-reviewer: no tests"


def test_synthesize_error_with_empty_message_has_fallback():
    # Don't leak a trailing "<name>: " into the rationale when error is empty.
    result = _synthesize([
        _ro("qa-evidence", "PASS"),
        _ro("code-reviewer", "ERROR", error=""),
    ])
    assert result["verdict"] == "BLOCK"
    assert "code-reviewer: (no error message)" in result["rationale"]


def test_qa_evidence_prompt_includes_run_path():
    prompt = build_qa_evidence_prompt(ticket_key="PROJ-123", run_name="2026-06-07_15-32-11")
    assert "PROJ-123" in prompt
    assert "2026-06-07_15-32-11" in prompt
    assert "summary.json" in prompt
    assert "VERDICT:" in prompt


def test_code_reviewer_prompt_lists_prs():
    pr_refs = [
        {"repo": "service-cms", "pr_id": "1234", "title": "Add foo"},
        {"repo": "service-a",   "pr_id": "987",  "title": "Fix bar"},
    ]
    prompt = build_code_reviewer_prompt(ticket_key="PROJ-123", pr_refs=pr_refs, diffs={"service-cms/1234": "diff text 1", "service-a/987": "diff text 2"})
    assert "PROJ-123" in prompt
    assert "service-cms" in prompt and "1234" in prompt
    assert "service-a" in prompt and "987" in prompt
    assert "diff text 1" in prompt and "diff text 2" in prompt
    assert "VERDICT:" in prompt


def test_code_reviewer_prompt_handles_no_prs():
    prompt = build_code_reviewer_prompt(ticket_key="PROJ-123", pr_refs=[], diffs={})
    # With no PRs the reviewer should default to PASS — there's nothing
    # to review. The prompt should make that explicit.
    assert "no pull requests" in prompt.lower() or "no prs" in prompt.lower()
    assert "VERDICT:" in prompt


def test_code_reviewer_prompt_truncates_huge_diffs():
    # Pin the truncation behavior so a future bump to _MAX_DIFF_CHARS
    # doesn't silently change reviewer context size.
    huge = "x" * 100_000
    pr_refs = [{"repo": "service-cms", "pr_id": "1234", "title": "Big PR"}]
    prompt = build_code_reviewer_prompt(
        ticket_key="PROJ-123",
        pr_refs=pr_refs,
        diffs={"service-cms/1234": huge},
    )
    assert "x" * 80_000 in prompt
    assert "x" * 80_001 not in prompt
    assert "diff truncated, original was 100000 chars" in prompt


def test_append_audit_writes_jsonl_line(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("council.COUNCIL_AUDIT_PATH", str(audit_path))
    append_audit({"event": "verdict", "ticket": "PROJ-1", "verdict": "PASS"})
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = _json.loads(lines[0])
    assert record["ticket"] == "PROJ-1"
    assert record["verdict"] == "PASS"
    assert "at" in record  # ISO timestamp added by writer
    assert record["at"].endswith("Z")  # pin ISO-Z format contract for downstream parsers


def test_append_audit_appends_not_overwrites(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("council.COUNCIL_AUDIT_PATH", str(audit_path))
    append_audit({"ticket": "A"})
    append_audit({"ticket": "B"})
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_run_reviewer_pass(monkeypatch):
    _set_stub(monkeypatch, "pass")
    rv = Reviewer(name="qa-evidence", prompt_builder=lambda **kw: "irrelevant — stub ignores prompt")
    outcome = await _run_reviewer(rv, ctx={"ticket_key": "PROJ-1"})
    assert outcome["name"] == "qa-evidence"
    assert outcome["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_run_reviewer_block(monkeypatch):
    _set_stub(monkeypatch, "block")
    rv = Reviewer(name="code-reviewer", prompt_builder=lambda **kw: "irrelevant")
    outcome = await _run_reviewer(rv, ctx={"ticket_key": "PROJ-1"})
    assert outcome["verdict"] == "BLOCK"
    assert "null check" in outcome["reason"]


@pytest.mark.asyncio
async def test_run_reviewer_no_verdict(monkeypatch):
    _set_stub(monkeypatch, "no_verdict")
    rv = Reviewer(name="code-reviewer", prompt_builder=lambda **kw: "irrelevant")
    outcome = await _run_reviewer(rv, ctx={"ticket_key": "PROJ-1"})
    assert outcome["verdict"] is None
    # Full stdout retained for debugging
    assert "forgot the verdict" in outcome["stdout"]


def test_synthesize_carries_per_reviewer_usage():
    outcomes = [
        {"name": "qa-evidence", "verdict": "PASS", "reason": "", "error": None,
         "model": "claude-haiku-4-5",
         "usage": {"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 20}},
        {"name": "code-reviewer", "verdict": "PASS", "reason": "", "error": None,
         "model": "default",
         "usage": {"cost_usd": 0.50, "input_tokens": 8000, "output_tokens": 900}},
    ]
    result = _synthesize(outcomes)
    by_name = {r["name"]: r for r in result["reviewers"]}
    assert by_name["qa-evidence"]["model"] == "claude-haiku-4-5"
    assert by_name["code-reviewer"]["usage"]["cost_usd"] == 0.50


def test_build_reviewer_cmd_includes_model_when_set():
    from council import _build_reviewer_cmd
    cmd = _build_reviewer_cmd("PROMPT TEXT", "claude-haiku-4-5")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"
    assert cmd[-1] == "PROMPT TEXT"   # prompt stays the final argv element


def test_build_reviewer_cmd_omits_model_when_none():
    from council import _build_reviewer_cmd
    cmd = _build_reviewer_cmd("PROMPT TEXT")
    assert "--model" not in cmd
    assert cmd[-1] == "PROMPT TEXT"


def test_default_reviewers_use_configured_models():
    # Policy (memory qa-no-haiku-testing): qa-evidence -> lower Sonnet, code-reviewer ->
    # Opus. Neither may be Haiku. (Was previously asserting code-reviewer inherits None.)
    import config
    from council import _default_reviewers
    by_name = {r.name: r for r in _default_reviewers()}
    assert by_name["qa-evidence"].model == config.QA_EVIDENCE_MODEL
    assert by_name["code-reviewer"].model == config.CODE_REVIEWER_MODEL
    assert "haiku" not in (by_name["qa-evidence"].model or "").lower()
    assert "haiku" not in (by_name["code-reviewer"].model or "").lower()


def test_fetch_diffs_uses_github_when_vcs_is_github(monkeypatch):
    """On the live GitHub instance the code reviewer must get REAL diffs (not empty
    Bitbucket) — otherwise it reviews nothing and rubber-stamps."""
    import asyncio
    import council, github_client, instance_config
    monkeypatch.setattr(instance_config, "load_instance_config",
                        lambda: {"vcs": {"type": "github"}})
    seen = {}

    def fake_diff(repo, pr_id, owner="Workabee-Technologies"):
        seen[(repo, pr_id)] = True
        return f"diff --git a/x b/x\n+for {repo}#{pr_id}"
    monkeypatch.setattr(github_client, "fetch_pr_diff", fake_diff)

    diffs = asyncio.run(council._fetch_diffs([{"repo": "xinventory-ux", "pr_id": "238"}]))
    assert "for xinventory-ux#238" in diffs["xinventory-ux/238"]
    assert ("xinventory-ux", 238) in seen        # pr_id coerced to int for the gh API
