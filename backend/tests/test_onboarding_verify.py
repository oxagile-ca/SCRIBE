"""Tests for onboarding_verify — the wizard's live connection checks.

Follows the house style: exercise the pure ``interpret_*`` / parser logic directly and
the credential-less early-return branches of the async wrappers (no httpx mocking).
"""
import onboarding_verify as ov


# ── interpret_linear ─────────────────────────────────────────────────────────

def test_linear_ok_when_viewer_and_team_visible():
    data = {"data": {"viewer": {"name": "Ankit", "email": "a@x.io"},
                      "teams": {"nodes": [{"key": "NOR"}, {"key": "ENG"}]}}}
    res = ov.interpret_linear(200, data, ["NOR"])
    assert res["ok"] is True
    assert "NOR" in res["detail"] and "a@x.io" in res["detail"]


def test_linear_fail_when_team_not_visible_is_the_wrong_workspace_bug():
    # The exact failure we hit: token authenticates but can't see the configured team.
    data = {"data": {"viewer": {"email": "a@x.io"},
                     "teams": {"nodes": [{"key": "ENG"}]}}}
    res = ov.interpret_linear(200, data, ["NOR"])
    assert res["ok"] is False
    assert "NOR" in res["hint"]
    assert "different Linear workspace" in res["hint"]


def test_linear_fail_on_graphql_errors():
    res = ov.interpret_linear(200, {"errors": [{"message": "authentication failed"}]}, ["NOR"])
    assert res["ok"] is False
    assert "authentication failed" in res["hint"]


def test_linear_fail_on_401():
    res = ov.interpret_linear(401, None, ["NOR"])
    assert res["ok"] is False
    assert "401" in res["hint"]


def test_linear_ok_without_projects_still_needs_viewer():
    ok = ov.interpret_linear(200, {"data": {"viewer": {"name": "Ankit"}, "teams": {"nodes": []}}}, [])
    assert ok["ok"] is True
    bad = ov.interpret_linear(200, {"data": {"viewer": {}}}, [])
    assert bad["ok"] is False


# ── interpret_github ─────────────────────────────────────────────────────────

def test_github_status_mapping():
    assert ov.interpret_github(200, "o", "r")["ok"] is True
    assert ov.interpret_github(401, "o", "r")["ok"] is False
    assert "401" in ov.interpret_github(401, "o", "r")["hint"]
    assert "404" in ov.interpret_github(404, "o", "r")["hint"]
    assert ov.interpret_github(403, "o", "r")["ok"] is False
    assert "500" in ov.interpret_github(500, "o", "r")["hint"]


# ── owner_repo_from_vcs ──────────────────────────────────────────────────────

def test_owner_repo_from_full_url_and_repos():
    o, r = ov.owner_repo_from_vcs({"org": "https://github.com/ferrolabsai6-dev/northstar-demo",
                                   "repos": ["northstar-demo"]})
    assert (o, r) == ("ferrolabsai6-dev", "northstar-demo")


def test_owner_repo_from_owner_url_plus_repos():
    o, r = ov.owner_repo_from_vcs({"org": "https://github.com/acme", "repos": ["widgets"]})
    assert (o, r) == ("acme", "widgets")


def test_owner_repo_from_url_only_when_no_repos():
    o, r = ov.owner_repo_from_vcs({"org": "https://github.com/acme/widgets", "repos": []})
    assert (o, r) == ("acme", "widgets")


def test_owner_repo_empty_when_nothing_useful():
    assert ov.owner_repo_from_vcs({"org": "", "repos": []}) == ("", "")


# ── interpret_environment ────────────────────────────────────────────────────

def test_environment_reachable_vs_error():
    assert ov.interpret_environment(200, "http://x")["ok"] is True
    assert ov.interpret_environment(302, "http://x")["ok"] is True
    assert ov.interpret_environment(404, "http://x")["ok"] is False
    assert ov.interpret_environment(503, "http://x")["ok"] is False


# ── interpret_anthropic ──────────────────────────────────────────────────────

def test_anthropic_status_mapping():
    assert ov.interpret_anthropic(0, has_key=False)["ok"] is True
    assert ov.interpret_anthropic(200, has_key=True)["ok"] is True
    assert ov.interpret_anthropic(401, has_key=True)["ok"] is False


# ── async wrappers: credential-less early returns (no network) ────────────────

async def test_verify_issue_tracker_requires_token():
    res = await ov.verify_issue_tracker({"issueTracker": {"type": "linear", "token": ""}})
    assert res["ok"] is False and "token" in res["hint"].lower()


async def test_verify_issue_tracker_unimplemented_type_is_not_blocked():
    # Azure/GitHub-issues have no live check yet — token present, don't block onboarding.
    res = await ov.verify_issue_tracker({"issueTracker": {"type": "azure", "token": "x"}})
    assert res["ok"] is True


async def test_verify_issue_tracker_jira_without_base_url_fails():
    # Jira now gets a real check; no base URL is an actionable failure, not a pass.
    res = await ov.verify_issue_tracker({"issueTracker": {"type": "jira", "token": "x"}})
    assert res["ok"] is False
    assert "base url" in res["hint"].lower()


async def test_verify_vcs_requires_token_and_repo():
    assert (await ov.verify_vcs({"vcs": {"type": "github", "token": ""}}))["ok"] is False
    no_repo = await ov.verify_vcs({"vcs": {"type": "github", "token": "t", "org": "", "repos": []}})
    assert no_repo["ok"] is False


async def test_verify_environment_requires_url():
    assert (await ov.verify_environment({"environments": {"staticUrls": []}}))["ok"] is False


async def test_verify_anthropic_no_key_is_ok():
    assert (await ov.verify_anthropic({"anthropicKey": ""}))["ok"] is True


async def test_verify_dispatch_unknown_target():
    res = await ov.verify("bogus", {})
    assert res["ok"] is False and "Unknown" in res["hint"]


# ── interpret_jira ───────────────────────────────────────────────────────────
# The onboarding "Test connection" used to just say "token present" for Jira —
# no real check. These pin a real verdict from a /rest/api/3/myself response so
# the user can tell whether the email + API token are actually right.

def test_jira_ok_when_myself_returns_a_user():
    res = ov.interpret_jira(200, {"emailAddress": "ankit@x.com", "displayName": "Ankit"}, "ankit@x.com")
    assert res["ok"] is True
    assert "ankit@x.com" in res["detail"]


def test_jira_fail_on_401_bad_token():
    res = ov.interpret_jira(401, None, "ankit@x.com")
    assert res["ok"] is False
    assert "token" in res["hint"].lower()


def test_jira_fail_on_403():
    res = ov.interpret_jira(403, None, "ankit@x.com")
    assert res["ok"] is False


def test_jira_redirect_means_bad_base_url():
    # A /browse/... or trailing-slash base URL 302s to login — call it out as a base-URL problem.
    res = ov.interpret_jira(302, None, "ankit@x.com")
    assert res["ok"] is False
    assert "base url" in res["hint"].lower()


def test_jira_404_means_bad_base_url():
    res = ov.interpret_jira(404, None, "ankit@x.com")
    assert res["ok"] is False
    assert "base url" in res["hint"].lower()


def test_jira_200_without_user_is_a_failure():
    # 200 but no user body (e.g. anonymous/HTML) — not a valid auth.
    res = ov.interpret_jira(200, {}, "ankit@x.com")
    assert res["ok"] is False
