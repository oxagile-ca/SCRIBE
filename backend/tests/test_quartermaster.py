import asyncio
import json
import pathlib
import pytest
from unittest.mock import AsyncMock, patch

from quartermaster import (
    resolve_deployables,
    ensure_env,
    ensure_snapshots,
    deploy_snapshots,
    provision_env,
    is_env_ready_for_qa,
    _wait_env_settled,
    renew_parent_env_lease,
)
from config import AUTO_PROVISION_PARENT_ENV, AUTO_PROVISION_PARENT_KEEPALIVE_HOURS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return (FIXTURES / name).read_text()


@pytest.mark.asyncio
async def test_resolve_deployables_returns_services_for_deployable_service():
    prs = [{"repo": "service-a", "branch": "feature/PROJ-404", "snapshot": "FEATURE-PROJ-404"}]
    with patch("quartermaster.bb.get_file", AsyncMock(return_value=_load("manifest_service.json"))):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == [("service-a", "FEATURE-PROJ-404")]
    assert skipped == []


@pytest.mark.asyncio
async def test_resolve_deployables_skips_libraries():
    prs = [{"repo": "lib-framework", "branch": "feature/x", "snapshot": "FEATURE-X"}]
    with patch("quartermaster.bb.get_file", AsyncMock(return_value=_load("manifest_library.json"))):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == []
    assert skipped == [("lib-framework", "library")]


@pytest.mark.asyncio
async def test_resolve_deployables_skips_missing_field():
    prs = [{"repo": "weird-repo", "branch": "main", "snapshot": "SNAP"}]
    with patch("quartermaster.bb.get_file", AsyncMock(return_value=_load("manifest_missing_field.json"))):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == []
    assert skipped == [("weird-repo", "no deployable field")]


@pytest.mark.asyncio
async def test_resolve_deployables_skips_missing_manifest():
    prs = [{"repo": "no-manifest", "branch": "main", "snapshot": "SNAP"}]
    with patch("quartermaster.bb.get_file", AsyncMock(return_value=None)):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == []
    assert skipped == [("no-manifest", "no manifest")]


@pytest.mark.asyncio
async def test_resolve_deployables_handles_invalid_json():
    prs = [{"repo": "broken", "branch": "main", "snapshot": "SNAP"}]
    with patch("quartermaster.bb.get_file", AsyncMock(return_value="not json {")):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == []
    assert skipped == [("broken", "invalid manifest json")]


@pytest.mark.asyncio
async def test_resolve_deployables_mixed_list():
    prs = [
        {"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"},
        {"repo": "lib-framework", "branch": "feature/x", "snapshot": "SNAP-B"},
    ]
    manifests = {
        "service-a": _load("manifest_service.json"),
        "lib-framework": _load("manifest_library.json"),
    }

    async def fake_get_file(repo, branch, path):
        return manifests[repo]

    with patch("quartermaster.bb.get_file", AsyncMock(side_effect=fake_get_file)):
        deployables, skipped = await resolve_deployables(prs)
    assert deployables == [("service-a", "SNAP-A")]
    assert skipped == [("lib-framework", "library")]


class _FakeStream:
    def __init__(self):
        self.events = []
    def append(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_ensure_env_creates_when_missing():
    stream = _FakeStream()
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b"[]", b""))
    ls_proc.returncode = 0
    create_proc = AsyncMock()
    create_proc.communicate = AsyncMock(return_value=(b"created", b""))
    create_proc.returncode = 0
    # _wait_env_settled polls ls once; return env with no services (treated as settled).
    settle_proc = AsyncMock()
    settle_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-404","services":[]}]', b""))
    settle_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc, create_proc, settle_proc]) as spawn:
        await ensure_env("proj-404", stream)

    cmds = [tuple(call.args) for call in spawn.call_args_list]
    assert cmds[0][:3] == ("deploycli", "deploy", "ls")
    assert cmds[1][:3] == ("deploycli", "deploy", "create")
    assert "proj-404" in cmds[1]
    assert "--method" in cmds[1]
    assert "clone" in cmds[1]
    # Clone replicates services AND resources, so --services-only must NOT
    # be present — that flag would strip every resource from the new env.
    assert "--services-only" not in cmds[1]
    # --wait must NOT be passed: deploycli's --wait poll has thrown transient
    # "Environment not found" mid-create and rolled back real envs. We use
    # our own _wait_env_settled (with visibility grace) instead.
    assert "--wait" not in cmds[1]
    # Post-create settle loop must run.
    assert cmds[2][:3] == ("deploycli", "deploy", "ls")


@pytest.mark.asyncio
async def test_ensure_env_renews_when_exists():
    stream = _FakeStream()
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-404"}]', b""))
    ls_proc.returncode = 0
    renew_proc = AsyncMock()
    renew_proc.communicate = AsyncMock(return_value=(b"renewed", b""))
    renew_proc.returncode = 0
    # _wait_env_settled polls ls once; treat empty services as settled.
    settle_proc = AsyncMock()
    settle_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-404","services":[]}]', b""))
    settle_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc, renew_proc, settle_proc]) as spawn:
        await ensure_env("proj-404", stream)

    cmds = [tuple(call.args) for call in spawn.call_args_list]
    assert cmds[1][:3] == ("deploycli", "deploy", "renew")
    # Renew path must ALSO run the settle check so leftover in-flight Jenkins
    # jobs from a prior failed deploy don't race the next snapshot deploy.
    assert cmds[2][:3] == ("deploycli", "deploy", "ls")


@pytest.mark.asyncio
async def test_ensure_env_continues_when_renew_fails():
    """Renew failures on existing envs must be non-fatal: the env is already
    up, and the deploycli CLI has hung on renew in the past — we don't want a
    stuck renew blocking the deploy step."""
    stream = _FakeStream()
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-406"}]', b""))
    ls_proc.returncode = 0
    renew_fail = AsyncMock()
    renew_fail.communicate = AsyncMock(return_value=(b"", b"renew kaput"))
    renew_fail.returncode = 1
    settle_proc = AsyncMock()
    settle_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-406","services":[]}]', b""))
    settle_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc, renew_fail, settle_proc]) as spawn:
        # Must NOT raise.
        await ensure_env("proj-406", stream)

    cmds = [tuple(call.args) for call in spawn.call_args_list]
    assert cmds[1][:3] == ("deploycli", "deploy", "renew")
    assert cmds[2][:3] == ("deploycli", "deploy", "ls")
    logs = [e["data"] for e in stream.events if e.get("type") == "log"]
    assert any("renew warning" in m and "renew kaput" in m for m in logs)


@pytest.mark.asyncio
async def test_ensure_env_waits_on_renew_when_services_still_deploying(monkeypatch):
    """Renew path must also block on in-flight services (lock-contention defence)."""
    stream = _FakeStream()
    ls_exists_proc = AsyncMock()
    ls_exists_proc.communicate = AsyncMock(return_value=(b'[{"name":"proj-404"}]', b""))
    ls_exists_proc.returncode = 0
    renew_proc = AsyncMock()
    renew_proc.communicate = AsyncMock(return_value=(b"renewed", b""))
    renew_proc.returncode = 0
    # Settle loop: first poll shows DEPLOYING, second poll clears.
    in_flight = b'[{"name":"proj-404","services":[{"name":"service-a","status":"DEPLOYING"}]}]'
    settled = b'[{"name":"proj-404","services":[{"name":"service-a","status":"STABLE"}]}]'
    in_flight_proc = AsyncMock()
    in_flight_proc.communicate = AsyncMock(return_value=(in_flight, b""))
    in_flight_proc.returncode = 0
    settled_proc = AsyncMock()
    settled_proc.communicate = AsyncMock(return_value=(settled, b""))
    settled_proc.returncode = 0

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_exists_proc, renew_proc, in_flight_proc, settled_proc]) as spawn:
        await ensure_env("proj-404", stream)

    cmds = [tuple(call.args) for call in spawn.call_args_list]
    assert cmds[1][:3] == ("deploycli", "deploy", "renew")
    assert cmds[2][:3] == ("deploycli", "deploy", "ls")
    assert cmds[3][:3] == ("deploycli", "deploy", "ls")
    logs = [e["data"] for e in stream.events if e.get("type") == "log"]
    assert any("Waiting for" in m and "service-a" in m for m in logs)
    assert any("settled" in m for m in logs)


@pytest.mark.asyncio
async def test_ensure_env_raises_on_create_failure():
    stream = _FakeStream()
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b"[]", b""))
    ls_proc.returncode = 0
    create_proc = AsyncMock()
    create_proc.communicate = AsyncMock(return_value=(b"", b"deploy capacity exceeded"))
    create_proc.returncode = 1

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc, create_proc]):
        with pytest.raises(RuntimeError) as exc:
            await ensure_env("proj-404", stream)
        assert "ensure_env" in str(exc.value)
        assert "capacity" in str(exc.value)


@pytest.mark.asyncio
async def test_ensure_snapshots_skips_when_artifact_exists():
    """When snapshot_artifact_exists returns 'exists', skip build and log 'already exists'."""
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]
    deployables = [("service-a", "SNAP-A")]

    async def fake_run_build(repo, branch):
        yield {"type": "log", "data": "should not be called"}

    with patch("quartermaster.agents.snapshot_artifact_exists",
               AsyncMock(return_value=("exists", "3.1.0-SNAP-A", []))):
        with patch("quartermaster.agents.run_build", side_effect=fake_run_build) as mock_build:
            await ensure_snapshots(deployables, prs, stream)

    log_messages = [e["data"] for e in stream.events if e.get("type") == "log"]
    assert any("already exists" in msg for msg in log_messages)
    mock_build.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_snapshots_builds_when_missing():
    """When snapshot_artifact_exists returns 'missing', build and emit all events."""
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]
    deployables = [("service-a", "SNAP-A")]

    async def fake_run_build(repo, branch, service=None, snapshot=None):
        yield {"type": "log", "data": "compiling"}
        yield {"type": "done", "success": True}

    with patch("quartermaster.agents.snapshot_artifact_exists",
               AsyncMock(return_value=("missing", "", []))):
        with patch("quartermaster.agents.run_build", side_effect=fake_run_build):
            await ensure_snapshots(deployables, prs, stream)

    log_messages = [e["data"] for e in stream.events if e.get("type") == "log"]
    assert any("building service-a from service-a@feature/x" in msg for msg in log_messages)
    # The two yielded events from fake_run_build must be in stream
    assert any(e == {"type": "log", "data": "compiling"} for e in stream.events)
    assert any(e == {"type": "done", "success": True} for e in stream.events)


@pytest.mark.asyncio
async def test_ensure_snapshots_raises_when_build_fails():
    """When run_build yields a done event with success=False, raise RuntimeError."""
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]
    deployables = [("service-a", "SNAP-A")]

    async def fake_run_build_fail(repo, branch, service=None, snapshot=None):
        yield {"type": "done", "success": False, "msg": "compile error"}

    with patch("quartermaster.agents.snapshot_artifact_exists",
               AsyncMock(return_value=("missing", "", []))):
        with patch("quartermaster.agents.run_build", side_effect=fake_run_build_fail):
            with pytest.raises(RuntimeError) as exc:
                await ensure_snapshots(deployables, prs, stream)

    assert "compile error" in str(exc.value) or "build failed" in str(exc.value)


@pytest.mark.asyncio
async def test_deploy_snapshots_invokes_run_deploy_per_service():
    stream = _FakeStream()
    deployables = [("service-a", "SNAP-A"), ("service-b", "SNAP-H")]

    async def _fake_deploy(env, service, snapshot):
        yield {"type": "log", "data": f"deploying {service}"}
        yield {"type": "done", "success": True}

    with patch("quartermaster.agents.run_deploy", side_effect=_fake_deploy):
        await deploy_snapshots("proj-404", deployables, stream)

    logs = [e["data"] for e in stream.events if e["type"] == "log"]
    assert any("deploying service-a @ SNAP-A onto proj-404" in log for log in logs)
    assert any("deploying service-b @ SNAP-H onto proj-404" in log for log in logs)


@pytest.mark.asyncio
async def test_deploy_snapshots_raises_when_deploy_fails():
    stream = _FakeStream()
    deployables = [("service-a", "SNAP-A")]

    async def _fake_deploy(env, service, snapshot):
        yield {"type": "done", "success": False, "msg": "pod unhealthy"}

    with patch("quartermaster.agents.run_deploy", side_effect=_fake_deploy):
        with pytest.raises(RuntimeError) as exc:
            await deploy_snapshots("proj-404", deployables, stream)
        assert "deploy_snapshots" in str(exc.value)
        assert "pod unhealthy" in str(exc.value)


@pytest.mark.asyncio
async def test_provision_env_happy_path_emits_done_ok():
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]

    with patch("quartermaster.ensure_env", AsyncMock()) as mock_ensure_env, \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "SNAP-A")], []))) as mock_resolve, \
         patch("quartermaster.ensure_snapshots", AsyncMock()) as mock_ensure_snapshots, \
         patch("quartermaster.deploy_snapshots", AsyncMock()) as mock_deploy:
        result = await provision_env("PROJ-404", prs, stream)

    assert result == {"status": "ok"}
    assert any(e.get("type") == "done" and e.get("status") == "ok" for e in stream.events)
    mock_ensure_env.assert_called_once()
    mock_resolve.assert_called_once()
    mock_ensure_snapshots.assert_called_once()
    mock_deploy.assert_called_once()


@pytest.mark.asyncio
async def test_provision_env_logs_skipped_non_deployables():
    stream = _FakeStream()
    prs = [
        {"repo": "service-a", "branch": "f", "snapshot": "A"},
        {"repo": "lib-framework", "branch": "f", "snapshot": "I"},
    ]

    with patch("quartermaster.ensure_env", AsyncMock()), \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "A")], [("lib-framework", "library")]))), \
         patch("quartermaster.ensure_snapshots", AsyncMock()), \
         patch("quartermaster.deploy_snapshots", AsyncMock()):
        await provision_env("PROJ-404", prs, stream)

    skip_logs = [e for e in stream.events if "skipped" in e.get("data", "").lower()]
    assert any("lib-framework" in e["data"] for e in skip_logs)


@pytest.mark.asyncio
async def test_provision_env_emits_done_failed_with_step_on_error():
    """When the parallel snapshot+env stage raises, the failure surfaces under
    the combined step label and the underlying error message propagates."""
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "f", "snapshot": "A"}]

    with patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "A")], []))), \
         patch("quartermaster.ensure_snapshots", AsyncMock()), \
         patch("quartermaster.ensure_env",
               AsyncMock(side_effect=RuntimeError("ensure_env: capacity"))):
        result = await provision_env("PROJ-404", prs, stream)

    assert result["status"] == "failed"
    assert result["step"] == "ensure_snapshots+ensure_env"
    assert "capacity" in result["reason"]
    done = [e for e in stream.events if e.get("type") == "done"][-1]
    assert done["status"] == "failed"
    assert done["step"] == "ensure_snapshots+ensure_env"


@pytest.mark.asyncio
async def test_provision_env_runs_snapshot_and_env_in_parallel():
    """ensure_snapshots and ensure_env must run concurrently, not sequentially.
    If they were sequential the second would only start after the first returned;
    we prove parallelism by having both wait on the same event before completing."""
    stream = _FakeStream()
    prs = [{"repo": "service-a", "branch": "f", "snapshot": "A"}]

    both_started = asyncio.Event()
    started_count = {"n": 0}

    async def _coordinated(*args, **kwargs):
        started_count["n"] += 1
        if started_count["n"] == 2:
            both_started.set()
        # Each coro will hang here until the OTHER one has also entered.
        # Sequential execution would deadlock (the second never starts);
        # parallel execution releases both.
        await asyncio.wait_for(both_started.wait(), timeout=2)

    with patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "A")], []))), \
         patch("quartermaster.ensure_snapshots", AsyncMock(side_effect=_coordinated)), \
         patch("quartermaster.ensure_env", AsyncMock(side_effect=_coordinated)), \
         patch("quartermaster.deploy_snapshots", AsyncMock()):
        result = await provision_env("PROJ-404", prs, stream)

    assert result == {"status": "ok"}
    assert started_count["n"] == 2


@pytest.mark.asyncio
async def test_is_env_ready_returns_false_when_env_missing():
    """If `deploycli ls` returns [], the env doesn't exist; not ready."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b"[]", b""))
    ls_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc]):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", [])

    assert ready is False
    assert expiration is None


@pytest.mark.asyncio
async def test_is_env_ready_returns_false_when_ls_fails():
    """If `deploycli ls` returns non-zero, treat as not-ready (env unknown)."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(return_value=(b"", b"deploy down"))
    ls_proc.returncode = 1

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[ls_proc]):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", [])

    assert ready is False
    assert expiration is None


@pytest.mark.asyncio
async def test_is_env_ready_returns_true_with_expiration_when_no_deployables():
    """Env exists, but PRs are all libraries — env is ready as-is."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(
        return_value=(b'[{"name":"proj-404","expiration":1750000000}]', b"")
    )
    ls_proc.returncode = 0

    prs = [{"repo": "lib-framework", "branch": "feature/x", "snapshot": "SNAP"}]

    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=[ls_proc]), \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([], [("lib-framework", "library")]))):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", prs)

    assert ready is True
    assert expiration == 1750000000


@pytest.mark.asyncio
async def test_is_env_ready_returns_true_when_all_snapshots_match():
    """Env exists + every deployable snapshot is on the env: ready."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(
        return_value=(b'[{"name":"proj-404","expiration":1750000000}]', b"")
    )
    ls_proc.returncode = 0

    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]

    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=[ls_proc]), \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "SNAP-A")], []))), \
         patch("quartermaster.agents.check_snapshot",
               AsyncMock(return_value=(True, "3.1.0-SNAP-A", "url", {}))):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", prs)

    assert ready is True
    assert expiration == 1750000000


@pytest.mark.asyncio
async def test_is_env_ready_returns_false_when_snapshot_not_deployed():
    """Env exists but a service runs the wrong version — not ready."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(
        return_value=(b'[{"name":"proj-404","expiration":1750000000}]', b"")
    )
    ls_proc.returncode = 0

    prs = [{"repo": "service-a", "branch": "feature/x", "snapshot": "SNAP-A"}]

    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=[ls_proc]), \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([("service-a", "SNAP-A")], []))), \
         patch("quartermaster.agents.check_snapshot",
               AsyncMock(return_value=(False, "k8s-stable", "url", {}))):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", prs)

    assert ready is False
    assert expiration == 1750000000


@pytest.mark.asyncio
async def test_is_env_ready_handles_object_ls_response():
    """deploycli ls sometimes returns a dict instead of a list."""
    ls_proc = AsyncMock()
    ls_proc.communicate = AsyncMock(
        return_value=(b'{"name":"proj-404","expiration":1750000000}', b"")
    )
    ls_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=[ls_proc]), \
         patch("quartermaster.resolve_deployables", AsyncMock(return_value=([], []))):
        ready, expiration = await is_env_ready_for_qa("PROJ-404", [])

    assert ready is True
    assert expiration == 1750000000


@pytest.mark.asyncio
async def test_provision_env_no_deployables_returns_ok_without_deploy():
    stream = _FakeStream()
    prs = [{"repo": "lib-framework", "branch": "feature/x", "snapshot": "SNAP-I"}]

    with patch("quartermaster.ensure_env", AsyncMock()), \
         patch("quartermaster.resolve_deployables",
               AsyncMock(return_value=([], [("lib-framework", "library")]))), \
         patch("quartermaster.ensure_snapshots", AsyncMock()) as mock_ensure_snapshots, \
         patch("quartermaster.deploy_snapshots", AsyncMock()) as mock_deploy:
        result = await provision_env("PROJ-404", prs, stream)

    assert result == {"status": "ok"}
    mock_ensure_snapshots.assert_not_called()
    mock_deploy.assert_not_called()
    logs = [e.get("data", "") for e in stream.events if e.get("type") == "log"]
    assert any("env ready as-is" in log for log in logs)
    assert any(e.get("type") == "done" and e.get("status") == "ok" for e in stream.events)


# --- _wait_env_settled tests ---------------------------------------------------


def _ls_proc(body: bytes, code: int = 0, err: bytes = b""):
    p = AsyncMock()
    p.communicate = AsyncMock(return_value=(body, err))
    p.returncode = code
    return p


@pytest.mark.asyncio
async def test_wait_env_settled_returns_immediately_when_idle(monkeypatch):
    """If every service is STABLE, settle returns on the first poll."""
    stream = _FakeStream()
    body = json.dumps([{
        "name": "proj-404",
        "services": [
            {"name": "service-a", "status": "STABLE"},
            {"name": "service-b", "status": "STABLE"},
        ],
    }]).encode()

    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[_ls_proc(body)]):
        await _wait_env_settled("proj-404", stream)

    assert sleeps == []
    logs = [e["data"] for e in stream.events if e.get("type") == "log"]
    assert any("settled" in m for m in logs)


@pytest.mark.asyncio
async def test_wait_env_settled_waits_until_in_flight_clears(monkeypatch):
    """Should keep polling while any service is DEPLOYING, then return."""
    stream = _FakeStream()
    in_flight = json.dumps([{
        "name": "proj-404",
        "services": [
            {"name": "service-a", "status": "DEPLOYING"},
            {"name": "service-b", "status": "STABLE"},
        ],
    }]).encode()
    settled = json.dumps([{
        "name": "proj-404",
        "services": [
            {"name": "service-a", "status": "STABLE"},
            {"name": "service-b", "status": "STABLE"},
        ],
    }]).encode()

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[_ls_proc(in_flight), _ls_proc(in_flight), _ls_proc(settled)]) as spawn:
        await _wait_env_settled("proj-404", stream)

    # Three polls: two in-flight, one settled.
    assert spawn.call_count == 3
    logs = [e["data"] for e in stream.events if e.get("type") == "log"]
    # First in-flight poll logs the waiting set; second in-flight poll has the
    # same set so it shouldn't log again (deduped). Settle logs once.
    waiting_logs = [m for m in logs if "Waiting for" in m]
    assert len(waiting_logs) == 1
    assert "service-a" in waiting_logs[0]
    assert any("settled" in m for m in logs)


@pytest.mark.asyncio
async def test_wait_env_settled_raises_on_timeout(monkeypatch):
    """Should raise RuntimeError once the deadline passes with services still in flight."""
    stream = _FakeStream()
    body = json.dumps([{
        "name": "proj-404",
        "services": [{"name": "service-a", "status": "DEPLOYING"}],
    }]).encode()

    # Fake the event-loop clock so we cross the deadline after one poll.
    times = iter([0.0, 99999.0, 99999.0])
    class _Loop:
        def time(self):
            return next(times)
    monkeypatch.setattr("quartermaster.asyncio.get_event_loop", lambda: _Loop())

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[_ls_proc(body), _ls_proc(body)]):
        with pytest.raises(RuntimeError) as exc:
            await _wait_env_settled("proj-404", stream)
    assert "timed out" in str(exc.value)
    assert "service-a" in str(exc.value)


@pytest.mark.asyncio
async def test_wait_env_settled_treats_empty_services_as_settled(monkeypatch):
    """A freshly-created env with no services is settled — nothing to wait on."""
    stream = _FakeStream()
    body = json.dumps([{"name": "proj-404", "services": []}]).encode()

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[_ls_proc(body)]) as spawn:
        await _wait_env_settled("proj-404", stream)
    assert spawn.call_count == 1


@pytest.mark.asyncio
async def test_wait_env_settled_raises_on_ls_failure(monkeypatch):
    """If ls returns non-zero, raise immediately rather than spin."""
    stream = _FakeStream()

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)

    with patch("quartermaster.asyncio.create_subprocess_exec",
               side_effect=[_ls_proc(b"", code=1, err=b"network down")]):
        with pytest.raises(RuntimeError) as exc:
            await _wait_env_settled("proj-404", stream)
    assert "ls failed" in str(exc.value)
    assert "network down" in str(exc.value)


@pytest.mark.asyncio
async def test_wait_env_settled_tolerates_not_found_during_grace_window(monkeypatch):
    """Right after create, `deploycli ls` can return 'Environment not found' while
    Deploy is still committing the env record. The settle loop must treat
    that as transient (retry) until the env becomes visible — not as a hard
    failure. Past incident: deploycli's own --wait poll raised on that exact 404 and
    we rolled back a real, deploying env."""
    stream = _FakeStream()
    not_found_err = b"error: Uncaught (in promise) InputError: Environment 'proj-404' not found"
    settled = json.dumps([{
        "name": "proj-404",
        "services": [{"name": "service-a", "status": "STABLE"}],
    }]).encode()

    # Two transient "not found" responses, then a real settled response.
    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)
    # Stay within the visibility grace window across polls.
    times = iter([0.0, 10.0, 20.0, 30.0, 40.0, 50.0])
    class _Loop:
        def time(self):
            return next(times)
    monkeypatch.setattr("quartermaster.asyncio.get_event_loop", lambda: _Loop())

    procs = [
        _ls_proc(b"", code=1, err=not_found_err),
        _ls_proc(b"", code=1, err=not_found_err),
        _ls_proc(settled),
    ]
    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=procs) as spawn:
        await _wait_env_settled("proj-404", stream)

    assert spawn.call_count == 3
    logs = [e["data"] for e in stream.events if e.get("type") == "log"]
    # The visibility-pending message should appear exactly once (deduped).
    pending = [m for m in logs if "not visible to Deploy yet" in m]
    assert len(pending) == 1
    assert any("settled" in m for m in logs)


@pytest.mark.asyncio
async def test_wait_env_settled_raises_when_not_found_persists_past_grace(monkeypatch):
    """If 'not found' keeps coming back after the grace window expires AND past
    the overall settle timeout, surface the failure (not an infinite wait)."""
    stream = _FakeStream()
    not_found_err = b"InputError: Environment 'proj-404' not found"

    async def fake_sleep(s):
        pass
    monkeypatch.setattr("quartermaster.asyncio.sleep", fake_sleep)
    # First poll at t=0 (in grace, tolerated). Second poll after deadline.
    times = iter([0.0, 0.0, 99999.0, 99999.0])
    class _Loop:
        def time(self):
            return next(times)
    monkeypatch.setattr("quartermaster.asyncio.get_event_loop", lambda: _Loop())

    procs = [
        _ls_proc(b"", code=1, err=not_found_err),
        _ls_proc(b"", code=1, err=not_found_err),
    ]
    with patch("quartermaster.asyncio.create_subprocess_exec", side_effect=procs):
        with pytest.raises(RuntimeError) as exc:
            await _wait_env_settled("proj-404", stream)
    assert "never became visible" in str(exc.value)


@pytest.mark.asyncio
async def test_renew_parent_env_lease_invokes_ivy_renew_for_parent():
    renew_proc = AsyncMock()
    renew_proc.communicate = AsyncMock(return_value=(b"renewed", b""))
    renew_proc.returncode = 0

    with patch("quartermaster.asyncio.create_subprocess_exec",
               return_value=renew_proc) as spawn:
        ok, msg = await renew_parent_env_lease()

    assert ok is True
    cmd = spawn.call_args_list[0].args
    assert cmd[:3] == ("deploycli", "deploy", "renew")
    assert AUTO_PROVISION_PARENT_ENV in cmd
    assert str(AUTO_PROVISION_PARENT_KEEPALIVE_HOURS) in cmd


@pytest.mark.asyncio
async def test_renew_parent_env_lease_returns_failure_on_non_zero():
    fail_proc = AsyncMock()
    fail_proc.communicate = AsyncMock(return_value=(b"", b"env not found"))
    fail_proc.returncode = 1

    with patch("quartermaster.asyncio.create_subprocess_exec", return_value=fail_proc):
        ok, msg = await renew_parent_env_lease()

    assert ok is False
    assert "env not found" in msg
