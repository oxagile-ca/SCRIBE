"""Tests for the GitHub VCS adapter — maps GitHub PR JSON to the pipeline's PR shape
(repo/branch/destBranch/prStatus, matching the Bitbucket adapter + _consolidate_prs)."""
from github_client import (
    normalize_pr, prs_for_ticket_from_list, pr_file_from_api,
    parse_pr_url, pr_refs_from_urls, format_unified_diff,
)

PR_OPEN = {
    "number": 12, "title": "Add SKU search", "state": "open", "merged_at": None,
    "head": {"ref": "feature/INV-602-payment", "sha": "abc1234"}, "base": {"ref": "main"},
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
    # head_sha is immutable — reconcile fetches PR-head blobs by it (a merged PR's
    # branch is often deleted, so fetching by branch name would 404).
    assert n["head_sha"] == "abc1234"
    assert normalize_pr(PR_MERGED, "r")["head_sha"] is None  # absent -> None
    assert normalize_pr(PR_MERGED, "r")["prStatus"] == "MERGED"
    assert normalize_pr(PR_CLOSED, "r")["prStatus"] == "DECLINED"  # closed, not merged


def test_prs_for_ticket_filters_by_branch_case_insensitively():
    out = prs_for_ticket_from_list([PR_OPEN, PR_MERGED, PR_CLOSED], "inv-602", "acme/cms")
    assert {p["branch"] for p in out} == {"feature/INV-602-payment", "INV-602-alt"}
    assert all(p["repo"] == "acme/cms" for p in out)


def test_prs_for_ticket_empty_when_no_match():
    assert prs_for_ticket_from_list([PR_MERGED], "INV-999", "r") == []


def test_pr_file_from_api_maps_filename_status_patch():
    row = {"filename": "src/fees.py", "status": "modified",
           "patch": "@@ -1 +1 @@\n-a\n+b\n", "additions": 1}
    assert pr_file_from_api(row) == {
        "path": "src/fees.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"}


def test_pr_file_from_api_patch_absent_for_binary_or_large_file():
    # GitHub omits `patch` for binary/large files — reconcile then compares blobs only.
    row = {"filename": "logo.png", "status": "added"}
    assert pr_file_from_api(row) == {"path": "logo.png", "status": "added", "patch": None}


def test_parse_pr_url_extracts_owner_repo_number():
    # The GitHub↔Linear integration attaches this exact URL shape to the issue.
    assert parse_pr_url("https://github.com/Workabee-Technologies/xinventory-ux/pull/238") == {
        "owner": "Workabee-Technologies", "repo": "xinventory-ux", "id": 238}
    # trailing path/fragment is tolerated
    assert parse_pr_url(
        "https://github.com/Workabee-Technologies/xinventory-services/pull/182/files#x"
    )["id"] == 182


def test_parse_pr_url_rejects_non_pr_urls():
    assert parse_pr_url("https://github.com/Workabee-Technologies/xinventory-ux/issues/5") is None
    assert parse_pr_url("https://linear.app/workabee/issue/INV-651") is None
    assert parse_pr_url("") is None
    assert parse_pr_url(None) is None


def test_format_unified_diff_renders_git_headers_for_code_reviewer():
    files = [
        {"path": "src/fees.ts", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"path": "logo.png", "status": "added", "patch": None},   # binary: no patch
    ]
    out = format_unified_diff(files)
    # a diff --git header the reviewer (and _extract_changed_files) recognizes
    assert "diff --git a/src/fees.ts b/src/fees.ts" in out
    assert "+b" in out
    # binary file noted but doesn't crash / emit a bogus patch
    assert "logo.png" in out


def test_format_unified_diff_empty():
    assert format_unified_diff([]) == ""


def test_pr_refs_from_urls_keeps_only_github_prs():
    urls = [
        "https://github.com/Workabee-Technologies/xinventory-ux/pull/238",
        "https://github.com/Workabee-Technologies/xinventory-ux/issues/9",  # not a PR
        "https://linear.app/x/issue/INV-651",                               # not github
    ]
    refs = pr_refs_from_urls(urls)
    assert refs == [{"owner": "Workabee-Technologies", "repo": "xinventory-ux", "id": 238}]
