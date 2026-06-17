"""Tests for cross-tracker status normalization (Jira / Linear / Azure name differently)."""
from status_map import categorize_status, resolve_status_mapping, DEFAULT_STATUS_MAP


def test_categorize_status_is_case_insensitive():
    m = {"ready_for_qa": ["Ready for testing"], "in_qa": ["In QA"]}
    assert categorize_status("Ready for testing", m) == "ready_for_qa"
    assert categorize_status("  ready for TESTING ", m) == "ready_for_qa"
    assert categorize_status("In QA", m) == "in_qa"
    assert categorize_status("In Progress", m) == "other"
    assert categorize_status("", m) == "other"


def test_resolve_uses_provider_default_when_no_config():
    m = resolve_status_mapping({}, "linear")
    assert "Ready for testing" in m["ready_for_qa"]
    j = resolve_status_mapping(None, "jira")
    assert "Ready for QA" in j["ready_for_qa"]


def test_resolve_prefers_config_override():
    cfg = {"issueTracker": {"statusMapping": {"ready_for_qa": ["QA Pending"], "in_qa": ["Verifying"]}}}
    m = resolve_status_mapping(cfg, "linear")
    assert m["ready_for_qa"] == ["QA Pending"]
    assert m["in_qa"] == ["Verifying"]


def test_resolve_fills_missing_key_from_provider_default():
    cfg = {"issueTracker": {"statusMapping": {"ready_for_qa": ["QA Pending"]}}}  # no in_qa
    m = resolve_status_mapping(cfg, "jira")
    assert m["ready_for_qa"] == ["QA Pending"]
    assert m["in_qa"] == DEFAULT_STATUS_MAP["jira"]["in_qa"]
