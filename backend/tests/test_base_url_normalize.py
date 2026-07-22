"""Tests for normalize_base_url.

A pasted ticket link or a trailing slash in the Jira base URL silently empties
the board: the code builds `{base}/rest/api/3/search/jql`, so
`.../browse/KEY/rest/...` 302s to login and `...net//rest/...` (double slash)
both return nothing with no error. This normalizes the value at both the config
read and the onboarding write.
"""
from instance_config import normalize_base_url


def test_strips_trailing_slash():
    assert normalize_base_url("https://x.atlassian.net/") == "https://x.atlassian.net"


def test_strips_pasted_browse_ticket_path():
    assert normalize_base_url("https://x.atlassian.net/browse/GHCMSE-1882") == "https://x.atlassian.net"


def test_strips_browse_path_with_trailing_slash():
    assert normalize_base_url("https://x.atlassian.net/browse/ABC-123/") == "https://x.atlassian.net"


def test_clean_url_is_unchanged():
    assert normalize_base_url("https://x.atlassian.net") == "https://x.atlassian.net"


def test_preserves_self_hosted_context_path():
    # Jira Server/DC can live under a context path; only the /browse/<key> ticket
    # suffix is a link, never a valid API base — so the context path is preserved.
    assert normalize_base_url("https://co.example.com/jira/") == "https://co.example.com/jira"
    assert normalize_base_url("https://co.example.com/jira/browse/ABC-1") == "https://co.example.com/jira"


def test_blank_and_none_are_safe():
    assert normalize_base_url("") == ""
    assert normalize_base_url(None) == ""


def test_strips_surrounding_whitespace():
    assert normalize_base_url("  https://x.atlassian.net/  ") == "https://x.atlassian.net"
