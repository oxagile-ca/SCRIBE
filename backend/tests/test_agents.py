"""Regression tests for the pipeline's pure functions.

These exist because today's PROJ-333 bug — deploying the wrong snapshot of an
e2e PR stacked on a feature PR — was discoverable with ~10 lines of test. Adding
that test first means the next change in this area can't quietly reintroduce it.
"""
from unittest.mock import patch
import pytest

import agents
from agents import (
    K8S_LABEL_LIMIT,
    _consolidate_prs,
    _is_trunk_dest,
    _resolve_test_env_url,
    _snapshot_matches,
)
from config import qa_target_host_for


# ─────────────────────────── _is_trunk_dest ───────────────────────────

class TestIsTrunkDest:
    def test_main_is_trunk(self):
        assert _is_trunk_dest("main") is True

    def test_master_develop_trunk(self):
        assert _is_trunk_dest("master") is True
        assert _is_trunk_dest("develop") is True

    def test_release_branch_is_trunk(self):
        assert _is_trunk_dest("release/2025-Q4") is True
        assert _is_trunk_dest("releases/v3") is True

    def test_uppercase_normalized(self):
        assert _is_trunk_dest("MAIN") is True

    def test_feature_branch_is_not_trunk(self):
        assert _is_trunk_dest("autoresolve/PROJ-333") is False
        assert _is_trunk_dest("qa/PROJ-333-e2e") is False

    def test_empty_and_none(self):
        assert _is_trunk_dest("") is False
        assert _is_trunk_dest(None) is False  # type: ignore[arg-type]


# ───────────────────────── _consolidate_prs ───────────────────────────

class TestConsolidatePrs:
    def test_proj_333_picks_feature_pr_not_stacked_e2e(self):
        """The exact regression: two open PRs on service-cms.
        Feature PR targets main, e2e PR stacks on the feature PR.
        Must pick the feature PR; must NOT iterate both."""
        prs = [
            {"repo": "acme/service-cms", "branch": "autoresolve/PROJ-333",
             "destBranch": "main", "prStatus": "OPEN"},
            {"repo": "acme/service-cms", "branch": "qa/PROJ-333-e2e",
             "destBranch": "autoresolve/PROJ-333", "prStatus": "OPEN"},
            {"repo": "acme/service-cms", "branch": "feature/PROJB-1668",
             "destBranch": "main", "prStatus": "DECLINED"},
        ]
        kept, dropped = _consolidate_prs(prs)

        assert len(kept) == 1
        assert kept[0]["branch"] == "autoresolve/PROJ-333"

        dropped_branches = {p["branch"] for p in dropped}
        assert "qa/PROJ-333-e2e" in dropped_branches
        assert "feature/PROJB-1668" in dropped_branches

        # Declined drop must say so; stacked drop must mention the parent branch.
        reasons_by_branch = {p["branch"]: p["reason"] for p in dropped}
        assert "DECLINED" in reasons_by_branch["feature/PROJB-1668"]
        assert "autoresolve/PROJ-333" in reasons_by_branch["qa/PROJ-333-e2e"]

    def test_single_pr_passes_through(self):
        prs = [{"repo": "acme/service-b", "branch": "feature/X",
                "destBranch": "main", "prStatus": "OPEN"}]
        kept, dropped = _consolidate_prs(prs)
        assert kept == prs
        assert dropped == []

    def test_multi_repo_keeps_one_per_repo(self):
        prs = [
            {"repo": "acme/service-b", "branch": "feature/X",
             "destBranch": "main", "prStatus": "OPEN"},
            {"repo": "acme/service-a", "branch": "feature/X",
             "destBranch": "main", "prStatus": "OPEN"},
        ]
        kept, dropped = _consolidate_prs(prs)
        repos = sorted(p["repo"] for p in kept)
        assert repos == ["acme/service-a", "acme/service-b"]
        assert dropped == []

    def test_declined_always_dropped(self):
        prs = [
            {"repo": "r", "branch": "a", "destBranch": "main", "prStatus": "DECLINED"},
            {"repo": "r", "branch": "b", "destBranch": "main", "prStatus": "OPEN"},
        ]
        kept, dropped = _consolidate_prs(prs)
        assert [p["branch"] for p in kept] == ["b"]
        assert dropped[0]["reason"] == "DECLINED"

    def test_stacked_on_release_branch(self):
        """When dest is release/* it counts as trunk — stacked PR loses."""
        prs = [
            {"repo": "r", "branch": "a", "destBranch": "release/v2", "prStatus": "OPEN"},
            {"repo": "r", "branch": "b", "destBranch": "a", "prStatus": "OPEN"},
        ]
        kept, dropped = _consolidate_prs(prs)
        assert [p["branch"] for p in kept] == ["a"]
        assert dropped[0]["branch"] == "b"

    def test_two_open_prs_both_trunk_picks_first_by_branch_name(self):
        """Pathological: two real PRs on same repo both targeting main.
        Deterministic tiebreak so logs are reproducible."""
        prs = [
            {"repo": "r", "branch": "feature/B", "destBranch": "main", "prStatus": "OPEN"},
            {"repo": "r", "branch": "feature/A", "destBranch": "main", "prStatus": "OPEN"},
        ]
        kept, dropped = _consolidate_prs(prs)
        assert [p["branch"] for p in kept] == ["feature/A"]
        assert dropped[0]["branch"] == "feature/B"

    def test_open_preferred_over_merged_on_tie(self):
        prs = [
            {"repo": "r", "branch": "feature/A", "destBranch": "main", "prStatus": "MERGED"},
            {"repo": "r", "branch": "feature/B", "destBranch": "main", "prStatus": "OPEN"},
        ]
        kept, _ = _consolidate_prs(prs)
        assert kept[0]["branch"] == "feature/B"

    def test_all_declined_returns_nothing(self):
        prs = [
            {"repo": "r", "branch": "a", "destBranch": "main", "prStatus": "DECLINED"},
        ]
        kept, dropped = _consolidate_prs(prs)
        assert kept == []
        assert len(dropped) == 1


# ───────────────────────── _snapshot_matches ──────────────────────────

class TestSnapshotMatches:
    def test_exact_match(self):
        assert _snapshot_matches("AUTORESOLVE-PROJ-333", "3.14.0-AUTORESOLVE-PROJ-333") is True

    def test_k8s_truncated_match(self):
        # K8s caps label values at 63 chars. A 60-char snapshot + "3.0.0-"
        # (6 chars) would be truncated to 57 chars of label, total = 63.
        exp = "FEATURE-PROJ-355-CMS-A-RIDICULOUSLY-LONG-BRANCH-NAME-XYZ123"   # 60 chars
        semver = "3.0.0-"                                                       # 6 chars
        available = K8S_LABEL_LIMIT - len(semver)                               # 57
        deployed = semver + exp[:available]                                     # total = 63
        assert len(deployed) == K8S_LABEL_LIMIT
        assert _snapshot_matches(exp, deployed) is True

    def test_mismatch(self):
        assert _snapshot_matches("FEATURE-A", "3.0.0-FEATURE-B") is False

    def test_empty_inputs(self):
        assert _snapshot_matches("", "3.0.0-FEATURE-A") is False
        assert _snapshot_matches("FEATURE-A", "") is False

    def test_short_truncation_does_not_false_positive(self):
        # A 2-char shared prefix shouldn't match — too easy to collide.
        assert _snapshot_matches("FEATURE-LONG-AND-DETAILED", "3.0.0-FE") is False

    def test_non_k8s_length_prefix_rejected(self):
        # Old logic accepted any prefix >= 20 chars. New logic only accepts
        # prefix-match when the deployed version is at the K8s limit (63 chars).
        # A 21-char prefix in a 27-char deployment is NOT truncation — it's
        # a different snapshot that happens to share a leading segment.
        exp = "FEATURE-PROJ-355-DIFFERENT"                                     # 27 chars
        deployed = "3.0.0-FEATURE-PROJ-355-SAME"                               # 28 chars total
        assert _snapshot_matches(exp, deployed) is False

    def test_two_long_branches_sharing_short_prefix(self):
        # Realistic false-positive that the old prefix logic could hit: two
        # branches on the same ticket with different suffixes, neither at the
        # K8s limit. They should NOT match each other.
        a = "FEATURE-PROJ-355-LONG-BRANCH-NAME-VARIANT-ALPHA"                  # 48 chars
        b_deployed = "3.0.0-FEATURE-PROJ-355-LONG-BRANCH-NAME-VARIANT-BETA"   # 53 chars total
        assert len(b_deployed) < K8S_LABEL_LIMIT  # truncation didn't happen
        assert _snapshot_matches(a, b_deployed) is False

    def test_truncation_only_accepted_at_exact_k8s_limit(self):
        # The deployed version must be at K8S_LABEL_LIMIT for prefix-match
        # to be accepted. One char short means it wasn't truncated by K8s,
        # so we shouldn't treat a shorter prefix as a truncated version.
        exp = "FEATURE-PROJ-355-CMS-A-RIDICULOUSLY-LONG-BRANCH-NAME-XYZ123"   # 60 chars
        semver = "3.0.0-"
        available = K8S_LABEL_LIMIT - len(semver)
        # Deployed is 1 char shorter than the K8s limit — not truncation.
        short_deployed = semver + exp[:available - 1]
        assert len(short_deployed) == K8S_LABEL_LIMIT - 1
        assert _snapshot_matches(exp, short_deployed) is False

    def test_real_world_short_snapshot(self):
        # A typical short snapshot from a real ticket should match exactly
        # without invoking the truncation path.
        assert _snapshot_matches("FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS",
                                 "3.466.0-FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS") is True


# ─────────────────────────── plugin → host-app test redirect ───────────────────────────

class TestHostFor:
    def test_non_plugin_is_identity(self):
        assert qa_target_host_for("service-cms") == "service-cms"
        assert qa_target_host_for("service-a") == "service-a"
        assert qa_target_host_for("service-assets-b") == "service-assets-b"

    def test_known_plugins_redirect_to_core_cms(self):
        for plugin in (
            "service-cms-base-plugin",
            "service-cms-plugin-b",
            "service-cms-plugin-c",
            "service-tools-plugin-d",
            "service-cms-plugin-e",
            "service-cms-plugin-f",
            "service-cms-plugin-g",
        ):
            assert qa_target_host_for(plugin) == "service-cms", plugin

    def test_unknown_service_falls_through(self):
        # New services we haven't catalogued yet should default to themselves
        # rather than silently routing somewhere unexpected.
        assert qa_target_host_for("something-not-in-the-map") == "something-not-in-the-map"


class TestResolveTestEnvUrl:
    """`_resolve_test_env_url` should query the *host* service URL for plugins,
    not the plugin's own host. We monkeypatch `check_snapshot` to record which
    service was looked up and to control the URL response."""

    @pytest.mark.asyncio
    async def test_plugin_only_ticket_resolves_to_core_cms(self, monkeypatch):
        looked_up = []

        async def fake_check_snapshot(env, service, snapshot):
            looked_up.append(service)
            if service == "service-cms":
                return True, "3.500.0", "https://service-cms-qa-env-1-qa.example/", {}
            return False, "", "", {}

        monkeypatch.setattr(agents, "check_snapshot", fake_check_snapshot)

        url = await _resolve_test_env_url("qa-env-1", ["service-cms-base-plugin"])
        assert url == "https://service-cms-qa-env-1-qa.example/"
        # The redirect must hit service-cms, never the plugin's own host.
        assert "service-cms-base-plugin" not in looked_up
        assert "service-cms" in looked_up

    @pytest.mark.asyncio
    async def test_mixed_plugin_and_host_dedups_lookup(self, monkeypatch):
        # When service-cms is also in the ticket, we should only hit it once even
        # if multiple plugins redirect to it.
        looked_up = []

        async def fake_check_snapshot(env, service, snapshot):
            looked_up.append(service)
            if service == "service-cms":
                return True, "3.500.0", "https://service-cms-qa-env-2-qa.example/", {}
            return False, "", "", {}

        monkeypatch.setattr(agents, "check_snapshot", fake_check_snapshot)

        url = await _resolve_test_env_url(
            "qa-env-2",
            ["service-cms-base-plugin", "service-cms", "service-cms-plugin-b"],
        )
        assert url == "https://service-cms-qa-env-2-qa.example/"
        assert looked_up.count("service-cms") == 1

    @pytest.mark.asyncio
    async def test_non_plugin_service_uses_its_own_url(self, monkeypatch):
        async def fake_check_snapshot(env, service, snapshot):
            if service == "service-a":
                return True, "1.0.0", "https://service-a-qa-env-qa.example/", {}
            return False, "", "", {}

        monkeypatch.setattr(agents, "check_snapshot", fake_check_snapshot)

        url = await _resolve_test_env_url("qa-env", ["service-a"])
        assert url == "https://service-a-qa-env-qa.example/"

    @pytest.mark.asyncio
    async def test_empty_when_nothing_resolves(self, monkeypatch):
        async def fake_check_snapshot(env, service, snapshot):
            return False, "", "", {}

        monkeypatch.setattr(agents, "check_snapshot", fake_check_snapshot)

        url = await _resolve_test_env_url("qa-env", ["service-cms-base-plugin"])
        assert url == ""


# ─────────────────────────── _builder_stage ───────────────────────────

@pytest.mark.asyncio
async def test_builder_fast_path_when_all_snapshots_deployed():
    """If check_deploy reports all snapshots already deployed on env, Builder
    emits a 'snapshots already staged' log and advances to Shipper without
    triggering deploycli build."""

    async def _fake_check(env, services):
        return {
            "allDeployed": True,
            "anyFailed": False,
            "services": [
                {"service": s["service"], "snapshot": s["snapshot"], "deployed": True}
                for s in services
            ],
        }

    events = []
    async for event in agents._builder_stage(
        env="proj-404",
        services=[{"service": "service-a", "snapshot": "FEATURE-PROJ-404"}],
        check_deploy_fn=_fake_check,
    ):
        events.append(event)

    log_msgs = [e["data"] for e in events if e["type"] == "log"]
    assert any("already staged" in m.lower() for m in log_msgs)
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["status"] == "ok"


@pytest.mark.asyncio
async def test_builder_falls_through_when_snapshots_missing():
    async def _fake_check(env, services):
        return {
            "allDeployed": False,
            "anyFailed": False,
            "services": [
                {"service": "service-a", "snapshot": "FEATURE-PROJ-404", "deployed": False}
            ],
        }

    saw_build_trigger = []

    async def _fake_build(repo, branch, service=None, snapshot=None):
        saw_build_trigger.append((repo, branch))
        yield {"type": "done", "status": "ok"}

    with patch("agents.run_build", side_effect=lambda r, b, **kw: _fake_build(r, b, **kw)):
        events = []
        async for event in agents._builder_stage(
            env="proj-404",
            services=[{
                "service": "service-a",
                "snapshot": "FEATURE-PROJ-404",
                "repo": "service-a",
                "branch": "feature/PROJ-404",
            }],
            check_deploy_fn=_fake_check,
        ):
            events.append(event)

    assert saw_build_trigger == [("service-a", "feature/PROJ-404")]
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["status"] == "ok"


@pytest.mark.asyncio
async def test_builder_emits_done_error_when_check_deploy_raises():
    async def _boom(env, services):
        raise RuntimeError("deploy unreachable")

    events = []
    async for event in agents._builder_stage(
        env="proj-404",
        services=[{"service": "service-a", "snapshot": "FEATURE-X"}],
        check_deploy_fn=_boom,
    ):
        events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["status"] == "error"
    log_msgs = [e["data"] for e in events if e["type"] == "log"]
    assert any("deploy unreachable" in m for m in log_msgs)


@pytest.mark.asyncio
async def test_builder_logs_skip_when_service_missing_repo_or_branch():
    async def _fake_check(env, services):
        return {"allDeployed": False, "anyFailed": False, "services": []}

    events = []
    async for event in agents._builder_stage(
        env="proj-404",
        services=[{"service": "service-a", "snapshot": "FEATURE-X"}],  # no repo/branch
        check_deploy_fn=_fake_check,
    ):
        events.append(event)

    log_msgs = [e["data"] for e in events if e["type"] == "log"]
    assert any("missing repo or branch" in m.lower() for m in log_msgs)
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["status"] == "ok"


@pytest.mark.asyncio
async def test_builder_emits_done_error_when_build_fails():
    async def _fake_check(env, services):
        return {"allDeployed": False, "anyFailed": False, "services": []}

    async def _fake_build_fail(repo, branch, service=None, snapshot=None):
        yield {"type": "done", "status": "fail"}

    with patch("agents.run_build", side_effect=lambda r, b, **kw: _fake_build_fail(r, b, **kw)):
        events = []
        async for event in agents._builder_stage(
            env="proj-404",
            services=[{
                "service": "service-a",
                "snapshot": "FEATURE-X",
                "repo": "service-a",
                "branch": "feature/x",
            }],
            check_deploy_fn=_fake_check,
        ):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    # Should see both the inner done (fail) AND our terminal done (error)
    assert any(d["status"] == "error" for d in done)


# ─────────── service-cms-base-plugin concrete-deps rule (Builder) ───────────

@pytest.mark.asyncio
async def test_builder_enforces_concrete_core_cms_and_apollo_for_plugin():
    """When service-cms-base-plugin is being built/deployed via the Builder path,
    service-cms AND service-a must be added as concrete live-release snapshot deploys
    before the fast-path deploy check runs. Otherwise a check that says
    'allDeployed' against just the plugin would skip the dep deploys entirely."""
    captured_services_for_check = []
    live_release = {"service-cms": "RELEASE-3-405-0", "service-a": "RELEASE-3-410-1"}

    async def _fake_resolve(service):
        return live_release.get(service)

    async def _fake_check(env, services):
        captured_services_for_check.append([
            (s.get("service"), s.get("snapshot")) for s in services
        ])
        return {"allDeployed": True, "anyFailed": False, "services": []}

    with patch("agents._resolve_live_release_snapshot_label", side_effect=_fake_resolve):
        events = []
        async for event in agents._builder_stage(
            env="proj-999",
            services=[{
                "service": "service-cms-base-plugin",
                "snapshot": "FEATURE-PROJ-999",
                "repo": "service-cms-base-plugin",
                "branch": "feature/PROJ-999",
            }],
            check_deploy_fn=_fake_check,
        ):
            events.append(event)

    # The check_deploy fn must have seen all three services, not just the plugin.
    assert captured_services_for_check, "check_deploy_fn was never invoked"
    expanded = dict(captured_services_for_check[0])
    assert expanded.get("service-cms-base-plugin") == "FEATURE-PROJ-999"
    assert expanded.get("service-cms") == "RELEASE-3-405-0"
    assert expanded.get("service-a") == "RELEASE-3-410-1"
    # Nothing in this set should reference k8s-stable as a snapshot label.
    for snap in expanded.values():
        assert "k8s-stable" not in (snap or "").lower()


@pytest.mark.asyncio
async def test_builder_fails_closed_when_live_release_unresolvable():
    """If neither/either of service-cms or service-a's live-release snapshot can be
    resolved, the Builder must emit done/error and never invoke the fast-path
    deploy check."""

    async def _fake_resolve_none(service):
        return None

    async def _fake_check(env, services):  # pragma: no cover — must not be reached
        raise AssertionError("check_deploy_fn must not be called when rule fails closed")

    with patch("agents._resolve_live_release_snapshot_label", side_effect=_fake_resolve_none):
        events = []
        async for event in agents._builder_stage(
            env="proj-999",
            services=[{
                "service": "service-cms-base-plugin",
                "snapshot": "FEATURE-PROJ-999",
                "repo": "service-cms-base-plugin",
                "branch": "feature/PROJ-999",
            }],
            check_deploy_fn=_fake_check,
        ):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["status"] == "error"
    log_msgs = [e["data"] for e in events if e["type"] == "log"]
    joined = " ".join(log_msgs).lower()
    assert "live-release" in joined or "concrete" in joined


# ─────────────────────────── snapshot_artifact_exists ───────────────────────────


@pytest.mark.asyncio
async def test_snapshot_artifact_exists_returns_exists_on_zero_exit():
    async def _fake_run_cmd(cmd, timeout=None):
        return 0, [
            "Found 3 snapshot matches or service-a. Using first found: 3.472.0-FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS.",
        ]

    with patch("agents._run_cmd_and_capture", side_effect=_fake_run_cmd):
        status, resolved, last5 = await agents.snapshot_artifact_exists(
            env="proj-404", service="service-a", snapshot="FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS"
        )
    assert status == "exists"
    assert resolved == "3.472.0-FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS."
    assert last5 == []


@pytest.mark.asyncio
async def test_snapshot_artifact_exists_treats_env_not_found_as_exists_when_artifact_resolved():
    """When the artifact resolves but the env doesn't (e.g. during create grace
    window or fresh ticket), we must NOT report missing — the snapshot is built."""
    async def _fake_run_cmd(cmd, timeout=None):
        # deploycli prints artifact resolution before failing on env lookup.
        return 1, [
            "Found 3 snapshot matches or service-a. Using first found: 3.472.0-FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS.",
            "error: Cannot retrieve environment qa-env from Deploy",
        ]

    with patch("agents._run_cmd_and_capture", side_effect=_fake_run_cmd):
        status, resolved, last5 = await agents.snapshot_artifact_exists(
            env=None, service="service-a", snapshot="FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS"
        )
    assert status == "exists"
    assert resolved == "3.472.0-FEAT-PROJ-404-RECIPE-TIME-TYPE-ENUMS."
    assert last5 == []


@pytest.mark.asyncio
async def test_snapshot_artifact_exists_returns_missing_when_no_artifact_line():
    async def _fake_run_cmd(cmd, timeout=None):
        return 1, [
            "error: Snapshot not found.",
            "Last 5 snapshots for service-a:",
            "3.471.0-MAIN",
            "3.470.0-MAIN",
            "3.469.0-MAIN",
            "3.468.0-MAIN",
            "3.467.0-MAIN",
        ]

    with patch("agents._run_cmd_and_capture", side_effect=_fake_run_cmd):
        status, resolved, last5 = await agents.snapshot_artifact_exists(
            env="proj-404", service="service-a", snapshot="FEAT-DOES-NOT-EXIST"
        )
    assert status == "missing"
    assert resolved == ""
    assert len(last5) == 5
    assert "3.471.0-MAIN" in last5


@pytest.mark.asyncio
async def test_snapshot_artifact_exists_returns_timeout_on_negative_one():
    async def _fake_run_cmd(cmd, timeout=None):
        return -1, []

    with patch("agents._run_cmd_and_capture", side_effect=_fake_run_cmd):
        status, resolved, last5 = await agents.snapshot_artifact_exists(
            env="proj-404", service="service-a", snapshot="FEAT-WHATEVER"
        )
    assert status == "timeout"
    assert resolved == ""
    assert last5 == []


# ─────────────────────────── run_build artifact polling ───────────────────────────


@pytest.mark.asyncio
async def test_run_build_returns_early_when_artifact_appears_after_poll_threshold():
    """Once we cross BUILD_ARTIFACT_POLL_START_SEC, the moment artifactory reports
    `exists` we should yield done=success and stop sleeping. That's the whole win."""
    fake_now = [0.0]

    def fake_time():
        return fake_now[0]

    async def fake_sleep(seconds):
        fake_now[0] += seconds

    async def fake_run_cmd(cmd, timeout=None):
        return 0, ["✅ Build started!"]

    artifact_calls = []

    async def fake_artifact_exists(env, service, snapshot):
        artifact_calls.append(fake_now[0])
        # First poll (t ≈ 16 min): not ready. Second poll: ready.
        if len(artifact_calls) == 1:
            return "missing", "", ["older-snapshot-1"]
        return "exists", f"service-a:3.475.0-{snapshot}", []

    with patch("agents._run_cmd_and_capture", side_effect=fake_run_cmd), \
         patch("agents.snapshot_artifact_exists", side_effect=fake_artifact_exists), \
         patch("agents.time.time", side_effect=fake_time), \
         patch("agents.asyncio.sleep", side_effect=fake_sleep):
        events = []
        async for event in agents.run_build("service-a", "feat/x", service="service-a", snapshot="FEAT-X"):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["success"] is True
    assert len(artifact_calls) == 2, "should have polled twice (miss, hit)"
    assert artifact_calls[0] >= agents.BUILD_ARTIFACT_POLL_START_SEC
    # Critically: we never reached the legacy 20-min blind-wait completion.
    assert fake_now[0] < agents.BUILD_ESTIMATED_SECONDS + agents.BUILD_ARTIFACT_POLL_INTERVAL * 4


@pytest.mark.asyncio
async def test_run_build_times_out_when_artifact_never_appears():
    """If artifactory never reports `exists`, fail at BUILD_ARTIFACT_MAX_SEC rather
    than waiting forever or returning a false success."""
    fake_now = [0.0]

    def fake_time():
        return fake_now[0]

    async def fake_sleep(seconds):
        fake_now[0] += seconds

    async def fake_run_cmd(cmd, timeout=None):
        return 0, ["✅ Build started!"]

    async def fake_artifact_exists(env, service, snapshot):
        return "missing", "", []

    with patch("agents._run_cmd_and_capture", side_effect=fake_run_cmd), \
         patch("agents.snapshot_artifact_exists", side_effect=fake_artifact_exists), \
         patch("agents.time.time", side_effect=fake_time), \
         patch("agents.asyncio.sleep", side_effect=fake_sleep):
        events = []
        async for event in agents.run_build("service-a", "feat/x", service="service-a", snapshot="FEAT-X"):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["success"] is False
    assert "timed out" in done[-1]["msg"].lower()


@pytest.mark.asyncio
async def test_run_build_uses_legacy_time_wait_when_no_snapshot_label():
    """Legacy callers that don't know the snapshot label up front must still get the
    old blind-time-wait behavior — never poll, never call snapshot_artifact_exists."""
    fake_now = [0.0]

    def fake_time():
        return fake_now[0]

    async def fake_sleep(seconds):
        fake_now[0] += seconds

    async def fake_run_cmd(cmd, timeout=None):
        return 0, ["✅ Build started!"]

    artifact_called = []

    async def fake_artifact_exists(env, service, snapshot):
        artifact_called.append(1)
        return "exists", "", []

    with patch("agents._run_cmd_and_capture", side_effect=fake_run_cmd), \
         patch("agents.snapshot_artifact_exists", side_effect=fake_artifact_exists), \
         patch("agents.time.time", side_effect=fake_time), \
         patch("agents.asyncio.sleep", side_effect=fake_sleep):
        events = []
        async for event in agents.run_build("service-a", "feat/x"):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["success"] is True
    assert artifact_called == [], "legacy path must not poll artifactory"
    assert fake_now[0] >= agents.BUILD_ESTIMATED_SECONDS


@pytest.mark.asyncio
async def test_run_build_propagates_trigger_failure_without_polling():
    """If the `deploycli build` trigger itself returns non-zero, fail
    immediately — don't fall into the poll loop."""
    async def fake_run_cmd(cmd, timeout=None):
        return 1, ["error: bad branch"]

    artifact_called = []

    async def fake_artifact_exists(env, service, snapshot):
        artifact_called.append(1)
        return "exists", "", []

    with patch("agents._run_cmd_and_capture", side_effect=fake_run_cmd), \
         patch("agents.snapshot_artifact_exists", side_effect=fake_artifact_exists):
        events = []
        async for event in agents.run_build("service-a", "feat/x", service="service-a", snapshot="FEAT-X"):
            events.append(event)

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["success"] is False
    assert "build trigger failed" in done[-1]["msg"].lower()
    assert artifact_called == []
