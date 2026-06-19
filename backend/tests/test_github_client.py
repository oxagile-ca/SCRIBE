"""Tests for the GitHub VCS adapter — maps GitHub PR JSON to the pipeline's PR shape
(repo/branch/destBranch/prStatus, matching the Bitbucket adapter + _consolidate_prs)."""
from github_client import normalize_pr, prs_for_ticket_from_list

PR_OPEN = {
    "number": 12, "title": "Add SKU search", "state": "open", "merged_at": None,
    "head": {"ref": "feature/INV-602-payment"}, "base": {"ref": "main"},
}
PR_MERGED = {
    "number": 9, "title": "fix", "state": "closed", "merged_at": "2026-06-10T00:00:00Z",
    "head": {"ref": "INV-500-fix"}, "base": {"ref": "develop"},
}
PR_CLOSED = {
    "number": 8, "title": "alt", "state": "closed", "merged_at": None,
    "head": {"ref": "INV-602-alt"}, "base": {"ref": "main"},
}


def test_normalize_pr_maps_branches_and_status():
    n = normalize_pr(PR_OPEN, "acme/cms")
    assert n["id"] == 12
    assert n["branch"] == "feature/INV-602-payment"
    assert n["destBranch"] == "main"
    assert n["prStatus"] == "OPEN"
    assert n["repo"] == "acme/cms"
    assert normalize_pr(PR_MERGED, "r")["prStatus"] == "MERGED"
    assert normalize_pr(PR_CLOSED, "r")["prStatus"] == "DECLINED"  # closed, not merged


def test_prs_for_ticket_filters_by_branch_case_insensitively():
    out = prs_for_ticket_from_list([PR_OPEN, PR_MERGED, PR_CLOSED], "inv-602", "acme/cms")
    assert {p["branch"] for p in out} == {"feature/INV-602-payment", "INV-602-alt"}
    assert all(p["repo"] == "acme/cms" for p in out)


def test_prs_for_ticket_empty_when_no_match():
    assert prs_for_ticket_from_list([PR_MERGED], "INV-999", "r") == []
