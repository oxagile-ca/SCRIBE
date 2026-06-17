"""Unit tests for qa_patterns classifier against ~/.claude/skills/qa-evidence/patterns.yml.

Tests assert that:
  - each rule fires on at least one positive trigger (file + keyword)
  - each rule does NOT fire on an obviously unrelated diff
  - empty diff + empty ticket text produces zero matches (no false positives)
  - inject_tcs_for_manifest produces well-shaped TC dicts
  - baseline_checklist returns the always-on lines
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import qa_patterns
from qa_patterns import (
    PatternMatch,
    classify,
    inject_tcs_for_manifest,
    baseline_checklist,
)


PATTERNS_PATH = Path.home() / ".claude" / "skills" / "qa-evidence" / "patterns.yml"


@pytest.fixture(autouse=True)
def _skip_if_no_patterns():
    if not PATTERNS_PATH.exists():
        pytest.skip(f"patterns.yml not present at {PATTERNS_PATH}")


def _ids(matches):
    return {m.id for m in matches}


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------

def test_empty_inputs_no_matches():
    matches = classify(changed_files=[], diff_text="", ticket_text="")
    assert matches == []


def test_unrelated_diff_no_matches():
    # README change with prose unrelated to any pattern keyword should not fire.
    matches = classify(
        changed_files=["docs/README.md"],
        diff_text="+ Updated some prose about onboarding and contact info.",
        ticket_text="Doc update for new hires",
    )
    assert matches == []


def test_match_dataclass_shape():
    matches = classify(
        changed_files=["src/blocks/Spotlight.tsx"],
        diff_text="",
        ticket_text="",
    )
    assert matches, "P3 file glob should fire on blocks/ path"
    m = matches[0]
    assert isinstance(m, PatternMatch)
    assert m.id and m.name and isinstance(m.matched_on, tuple)


# ---------------------------------------------------------------------------
# Positive cases — one file-trigger + one keyword-trigger per rule
# ---------------------------------------------------------------------------

POSITIVE_CASES = [
    # (rule_id, changed_files, ticket_text)
    ("P1_save_strip", ["src/publish/handler.ts"], "publish payload strip"),
    ("P2_reload_state", ["src/components/Doc.tsx"], "unsaved dirty badge"),
    ("P3_block_reorder", ["src/blocks/Spotlight.tsx"], "block reorder tiptap"),
    ("P4_concurrent_lock", ["src/locking/Lock.ts"], "concurrent takeover read-only"),
    ("P5_duplicate", ["src/duplicate/clone.ts"], "duplicate doc clone"),
    ("P6_validation", ["src/validation/required.ts"], "required field validation"),
    ("P7_metadata_seo", ["src/dtax/tags.ts"], "dtax seo taxene selene"),
    ("P8_upgrade_regression", ["frontend/package.json"], "upgrade vue 3 dependency"),
    ("P9_permissions", ["src/permissions/role.ts"], "permission role restricted reviewer"),
    ("P10_date_time", ["src/SchedulePicker.tsx"], "schedule date timezone 1969"),
    ("P11_preview", ["src/preview/render.ts"], "preview digioh embed"),
    ("P12_image_media", ["src/picker/ImagePicker.tsx"], "woodwing elvis image picker"),
    # P13 has no file triggers, only keywords
    ("P13_flaky", [], "save button validation message modal intermittent commerce block"),
    ("P14_security", ["package-lock.json"], "snyk vulnerability xss"),
]


@pytest.mark.parametrize("rule_id,files,ticket", POSITIVE_CASES)
def test_rule_fires_on_positive_case(rule_id, files, ticket):
    matches = classify(changed_files=files, diff_text="", ticket_text=ticket)
    ids = _ids(matches)
    assert rule_id in ids, f"{rule_id} expected to fire on {files!r} + {ticket!r}; got {ids}"


# ---------------------------------------------------------------------------
# Negative cases — keyword/file from one rule should not light up its neighbors
# ---------------------------------------------------------------------------

def test_p3_block_keyword_does_not_fire_p1_save_strip():
    matches = classify(
        changed_files=["src/blocks/Spotlight.tsx"],
        diff_text="",
        ticket_text="block reorder",
    )
    ids = _ids(matches)
    assert "P3_block_reorder" in ids
    assert "P1_save_strip" not in ids


def test_p8_package_json_does_not_fire_unrelated_rules():
    matches = classify(
        changed_files=["frontend/package.json"],
        diff_text="+ bumped lodash",
        ticket_text="upgrade dependency",
    )
    ids = _ids(matches)
    assert "P8_upgrade_regression" in ids
    # Should not fire random unrelated rules like P3 or P11
    assert "P3_block_reorder" not in ids
    assert "P11_preview" not in ids
    assert "P10_date_time" not in ids


def test_neutral_text_does_not_fire_p13_flaky():
    matches = classify(changed_files=[], diff_text="", ticket_text="add a button to the toolbar")
    assert "P13_flaky" not in _ids(matches)


# ---------------------------------------------------------------------------
# Diff-text keyword path (P1 keyword inside diff body)
# ---------------------------------------------------------------------------

def test_keyword_match_via_diff_text():
    diff = """diff --git a/src/x.ts b/src/x.ts
+ // sanitize the payload before publish
"""
    matches = classify(changed_files=["src/x.ts"], diff_text=diff, ticket_text="")
    assert "P1_save_strip" in _ids(matches)


# ---------------------------------------------------------------------------
# matched_on includes reason hints
# ---------------------------------------------------------------------------

def test_matched_on_reports_trigger_reason():
    matches = classify(
        changed_files=["src/blocks/Spotlight.tsx"],
        diff_text="",
        ticket_text="block",
    )
    p3 = next(m for m in matches if m.id == "P3_block_reorder")
    joined = " ".join(p3.matched_on)
    assert "file:" in joined or "kw:" in joined


# ---------------------------------------------------------------------------
# inject_tcs_for_manifest
# ---------------------------------------------------------------------------

def test_inject_tcs_shape():
    matches = classify(
        changed_files=["src/blocks/Spotlight.tsx"],
        diff_text="",
        ticket_text="block reorder",
    )
    tcs = inject_tcs_for_manifest(matches, ticket_key="PROJB-9999")
    assert tcs, "expected at least one injected TC for P3"
    for tc in tcs:
        assert tc["id"].startswith("TC-PAT-P3_block_reorder-")
        assert tc["title"]
        assert tc["priority"] in {"P0", "P1", "P2", "P3"}
        assert tc["type"] in {"automated", "manual"}
        assert isinstance(tc["evidence_required"], list) and tc["evidence_required"]
        assert "@PROJB-9999" in tc["tags"]
        assert "@qa-pattern" in tc["tags"]
        assert "@pattern-P3_block_reorder" in tc["tags"]
        assert isinstance(tc["assertion_hints"], list)


def test_inject_tcs_empty_when_no_matches():
    tcs = inject_tcs_for_manifest([], ticket_key="PROJ-1")
    assert tcs == []


# ---------------------------------------------------------------------------
# Baseline checklist
# ---------------------------------------------------------------------------

def test_baseline_checklist_non_empty():
    lines = baseline_checklist()
    assert isinstance(lines, list)
    assert lines, "baseline_always_on should not be empty"
    # At least one well-known line from the report should be present
    joined = " | ".join(lines).lower()
    assert "reload" in joined or "preview" in joined


# ---------------------------------------------------------------------------
# Patterns file override via env var
# ---------------------------------------------------------------------------

def test_missing_patterns_file_returns_no_matches(tmp_path, monkeypatch):
    missing = tmp_path / "nope.yml"
    matches = classify(
        changed_files=["src/blocks/Spotlight.tsx"],
        diff_text="",
        ticket_text="block",
        patterns_path=missing,
    )
    assert matches == []
