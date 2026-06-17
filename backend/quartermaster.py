"""
Quartermaster: provisions Deploy envs and deploys PR snapshots so the env
is test-ready by the time QA picks the ticket up.
"""

import asyncio
import json
import re
from typing import Dict, List, Optional, Tuple

import agents
import bitbucket_client as bb
from config import (
    AUTO_PROVISION_LEASE_HOURS,
    AUTO_PROVISION_OWNER,
    AUTO_PROVISION_PARENT_ENV,
    AUTO_PROVISION_PARENT_KEEPALIVE_HOURS,
)

# Services that MUST be co-deployed as concrete live-release snapshots whenever
# a service-cms-base-plugin PR is in the ticket. The plugin host (service-cms) and the
# data layer (service-a) must both pin to the current live release; leaving them as
# k8s-stable references has shipped bugs because the plugin loads against a
# moving target.
PLUGIN_TRIGGER_SERVICE = "service-cms-base-plugin"
PLUGIN_REQUIRED_CONCRETE_DEPS: Tuple[str, ...] = ("service-cms", "service-a")
LIVE_RELEASE_SOURCE_ENV = "k8s-stable"


async def resolve_live_release_snapshot(service: str) -> Optional[str]:
    """Return the snapshot label currently deployed on k8s-stable for `service`.

    k8s-stable IS the live release reference by definition — whatever version
    string deploycli reports for that service there is what production is running.
    We strip the leading `<semver>-` and return the rest (the snapshot label
    that `deploycli deploy --snapshot <LABEL>` accepts).

    Returns None if the service can't be looked up, the JSON is unparseable,
    or the version field doesn't contain a snapshot label after the semver
    (which can happen for pure reference deploys that don't carry a label).
    """
    code, lines = await agents._run_cmd_and_capture(
        f"deploycli --skip-update ls {LIVE_RELEASE_SOURCE_ENV} {service} --json",
        timeout=60,
    )
    if code != 0:
        return None
    raw = agents._strip_to_json("\n".join(lines))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    version = (data.get("version") or "").strip()
    if not version:
        return None
    # Strip the leading "<num>(.<num>)*-" semver to isolate the label.
    label = re.sub(r"^\d+(?:\.\d+)*-", "", version)
    if not label or label == version:
        # No semver prefix found — version is bare ("k8s-stable", "main", etc.).
        # That's a reference-only deploy with no concrete snapshot we can re-use.
        return None
    return label


async def enforce_core_cms_base_plugin_rule(
    deployables: List[Tuple[str, str]],
    stream,
) -> List[Tuple[str, str]]:
    """If `service-cms-base-plugin` is being deployed, ensure service-cms AND service-a
    ride along as concrete live-release snapshots.

    This is a fail-closed rule: if the live-release snapshot for either dep
    can't be resolved, we raise — refusing to deploy the plugin against a
    moving k8s-stable reference. The user can fix this by deploying a known
    concrete snapshot manually and retrying.

    Returns the (possibly expanded) deployables list. If the trigger service
    isn't present, returns the input unchanged.
    """
    service_names = {svc for svc, _snap in deployables}
    if PLUGIN_TRIGGER_SERVICE not in service_names:
        return deployables

    stream.append({
        "type": "log",
        "data": (
            f"{PLUGIN_TRIGGER_SERVICE} present — enforcing concrete live-release "
            f"deploys for {', '.join(PLUGIN_REQUIRED_CONCRETE_DEPS)}"
        ),
    })

    expanded = list(deployables)
    for dep in PLUGIN_REQUIRED_CONCRETE_DEPS:
        if dep in service_names:
            stream.append({
                "type": "log",
                "data": f"  {dep}: already in deployables (PR snapshot wins)",
            })
            continue
        stream.append({
            "type": "log",
            "data": f"  resolving live-release snapshot for {dep} from {LIVE_RELEASE_SOURCE_ENV}…",
        })
        snap = await resolve_live_release_snapshot(dep)
        if not snap:
            msg = (
                f"Cannot determine live-release snapshot for {dep}; refusing to "
                f"deploy {PLUGIN_TRIGGER_SERVICE} without concrete deps. "
                f"Manually deploy a concrete {dep} snapshot and retry."
            )
            stream.append({"type": "log", "data": msg})
            raise RuntimeError(msg)
        stream.append({
            "type": "log",
            "data": f"  {dep}: pinning to live-release snapshot {snap}",
        })
        expanded.append((dep, snap))
    return expanded


async def resolve_deployables(
    prs: List[Dict],
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """
    For each PR, fetch ci/manifest.json from the source branch and decide whether
    it's a deployable service.

    Args:
        prs: list of {"repo": str, "branch": str, "snapshot": str}

    Returns:
        (deployables, skipped) where:
          - deployables: [(service_name, snapshot_name), ...] for deployable services
          - skipped: [(repo, reason), ...] for non-deployables
    """
    deployables: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []

    for pr in prs:
        repo = pr["repo"]
        branch = pr["branch"]
        snapshot = pr["snapshot"]

        raw = await bb.get_file(repo, branch, "ci/manifest.json")
        if raw is None:
            skipped.append((repo, "no manifest"))
            continue

        try:
            manifest = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            skipped.append((repo, "invalid manifest json"))
            continue

        deployable = manifest.get("deployable")
        if deployable is None:
            skipped.append((repo, "no deployable field"))
            continue
        if deployable != "service":
            skipped.append((repo, str(deployable)))
            continue

        service = manifest.get("name", repo)
        deployables.append((service, snapshot))

    return deployables, skipped


_RUN_TIMEOUT_SEC = 30 * 60
# `deploycli renew` should complete in seconds. The deno-based deploycli CLI has
# hung indefinitely on renew in the past (~14 zombie processes accumulated over
# a day), so we cap renew calls tightly so a stuck one doesn't survive past the
# next scheduled run.
_RENEW_TIMEOUT_SEC = 120


async def _run(cmd: List[str], timeout: Optional[float] = _RUN_TIMEOUT_SEC) -> Tuple[int, str, str]:
    """Run a subprocess with a hard timeout. Past incidents: stale `deploycli
    renew/deploy` processes accumulated for 24+ hours because `_run` had no cap,
    blocking every subsequent pipeline poll on the same env. Caller can override
    via `timeout`; pass None to disable (only for tests)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return 124, "", f"_run: timed out after {timeout}s running: {' '.join(cmd)}"
    return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


# Statuses that indicate Deploy is still rolling a service out. If we
# fire snapshot deploys while any service shows one of these, the new
# Jenkins jobs will race the in-flight reference deploys for the env's
# service-registry global lock and fail with GlobalLockException.
_IN_FLIGHT_STATUSES = {"DEPLOYING", "PENDING", "STARTING", "QUEUED"}

# Hard cap on the post-create wait. Reference deploys typically settle in a
# few minutes; this is the upper bound before we give up.
ENV_SETTLE_TIMEOUT_SEC = 15 * 60
ENV_SETTLE_POLL_SEC = 20

# Right after `deploycli create` returns, Deploy's env record can take a
# moment to become queryable via `deploycli ls`. Within this window we treat the
# "Environment not found" / ls-failure responses as transient and keep polling.
ENV_VISIBILITY_GRACE_SEC = 90
_ENV_NOT_FOUND_RE = re.compile(r"Environment ['\"]?[^'\"]+['\"]? not found", re.IGNORECASE)


async def _wait_env_settled(env_name: str, stream) -> None:
    """Block until no service on `env_name` shows an in-flight Deploy status.

    Deploy's env-create with --method reference triggers a reference deploy
    for every service in the env, and the per-service jobs can still be rolling
    out after create returns. Firing PR-snapshot deploys against those services
    during that window races the in-flight jobs for the env's service-registry
    global lock and the new jobs lose with GlobalLockException. This helper
    polls `deploycli ls` and waits until every service has cleared the
    in-flight statuses.

    For a short grace window after create we tolerate `ls` returning
    "Environment not found" or other transient failures, since Deploy may
    not have committed the env record yet. Past the grace window, an ls
    failure is surfaced.

    Raises RuntimeError if the env hasn't settled inside ENV_SETTLE_TIMEOUT_SEC.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    deadline = start + ENV_SETTLE_TIMEOUT_SEC
    last_in_flight: List[str] = []
    announced_pending_visibility = False
    while True:
        code, out, err = await _run(["deploycli", "deploy", "ls", env_name, "--json"])
        elapsed = loop.time() - start
        in_grace = elapsed < ENV_VISIBILITY_GRACE_SEC

        if code != 0:
            combined = (err or "") + "\n" + (out or "")
            if in_grace and _ENV_NOT_FOUND_RE.search(combined):
                if not announced_pending_visibility:
                    stream.append({
                        "type": "log",
                        "data": f"Env {env_name} not visible to Deploy yet — waiting for record to commit",
                    })
                    announced_pending_visibility = True
                if loop.time() >= deadline:
                    raise RuntimeError(
                        f"_wait_env_settled: env {env_name} never became visible "
                        f"within {ENV_SETTLE_TIMEOUT_SEC}s"
                    )
                await asyncio.sleep(ENV_SETTLE_POLL_SEC)
                continue
            raise RuntimeError(
                f"_wait_env_settled: ls failed for {env_name}: {err.strip() or out.strip()}"
            )
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(f"_wait_env_settled: unparseable ls output for {env_name}")

        # ls returns either [env_obj] or env_obj; either way the service list
        # lives under "services". Empty/missing services means the env is fresh
        # — treat as settled (no in-flight deploys to wait on).
        if isinstance(data, list):
            env_info = data[0] if data else {}
        elif isinstance(data, dict):
            env_info = data
        else:
            env_info = {}
        services = env_info.get("services") or []

        in_flight = [
            (svc.get("name") or svc.get("service") or "?")
            for svc in services
            if (svc.get("status") or "").upper() in _IN_FLIGHT_STATUSES
        ]
        if not in_flight:
            stream.append({"type": "log", "data": f"Env {env_name} settled — all services idle"})
            return

        # Only log when the set changes, to keep the stream readable.
        if in_flight != last_in_flight:
            stream.append({
                "type": "log",
                "data": f"Waiting for {len(in_flight)} service(s) to settle: "
                        f"{', '.join(in_flight[:5])}{'…' if len(in_flight) > 5 else ''}",
            })
            last_in_flight = in_flight

        if loop.time() >= deadline:
            raise RuntimeError(
                f"_wait_env_settled: timed out after {ENV_SETTLE_TIMEOUT_SEC}s; "
                f"still in-flight: {', '.join(in_flight)}"
            )
        await asyncio.sleep(ENV_SETTLE_POLL_SEC)


async def ensure_env(env_name: str, stream) -> None:
    """Create the env if missing, or renew the lease if it already exists."""
    stream.append({"type": "log", "data": f"Checking env {env_name}…"})
    code, out, err = await _run(["deploycli", "deploy", "ls", env_name, "--json"])
    exists = False
    if code == 0:
        try:
            data = json.loads(out)
            exists = bool(data)
        except Exception:
            exists = False

    if exists:
        stream.append({"type": "log", "data": f"Env {env_name} exists — renewing {AUTO_PROVISION_LEASE_HOURS}h lease"})
        code, out, err = await _run([
            "deploycli", "deploy", "renew",
            "--env", env_name,
            "--hour", str(AUTO_PROVISION_LEASE_HOURS),
        ], timeout=_RENEW_TIMEOUT_SEC)
        if code != 0:
            # Renew failures on existing envs are non-fatal: env is already up
            # with whatever lease it has, and the deploy doesn't depend on the
            # renew completing. The deploycli CLI has hung indefinitely here in the
            # past; we don't want a stuck renew blocking the deploy.
            stream.append({"type": "log", "data": f"renew warning (continuing): {err.strip() or out.strip() or f'exit {code}'}"})
    else:
        stream.append({"type": "log", "data": f"Env {env_name} missing — cloning {AUTO_PROVISION_PARENT_ENV} ({AUTO_PROVISION_LEASE_HOURS}h lease)"})
        # Clone replicates services + resources from the parent. We use a
        # ~$1.20/hr PROJ env (qa-env-1) as the parent — cloning
        # service-cms-beta-testing produces ~$12/hr envs because that env carries
        # 66 real services. Trade-off: we must keep the parent's lease alive
        # ourselves (see renew_parent_env_lease, called from server startup).
        # Intentionally NOT passing --wait. deploycli's --wait runs pollEnvironmentStatus
        # which can throw `Environment 'X' not found` during the create-commit
        # window. Past incidents had deploycli exit non-zero on that transient 404 even
        # when Deploy eventually showed the env deploying. We use our own
        # _wait_env_settled (with a visibility grace window) instead, so a
        # CLI-side polling glitch can't fail-and-roll-back a real env.
        code, out, err = await _run([
            "deploycli", "deploy", "create",
            "-e", env_name,
            "-f", AUTO_PROVISION_PARENT_ENV,
            "--method", "clone",
            "--hour", str(AUTO_PROVISION_LEASE_HOURS),
            "--owner", AUTO_PROVISION_OWNER,
            "-y",
        ])
        if code != 0:
            raise RuntimeError(f"ensure_env: create failed for {env_name}: {err.strip() or out.strip()}")

    # After both create AND renew: wait until every service on the env has
    # cleared the in-flight statuses. `create --wait` only blocks on the
    # env-create record (not on the reference deploys it spawns), and renew
    # tells us nothing about Jenkins jobs left over from a prior failed
    # snapshot deploy. Either way, firing PR-snapshot deploys while services
    # are still rolling out races the env's service-registry global lock.
    await _wait_env_settled(env_name, stream)


def _branch_for_snapshot(snapshot: str, prs: List[Dict]) -> Optional[Tuple[str, str]]:
    """Find the (repo, branch) for a given snapshot by walking PR list."""
    for pr in prs:
        if pr.get("snapshot") == snapshot:
            return pr["repo"], pr["branch"]
    return None


async def ensure_snapshots(
    deployables: List[Tuple[str, str]],
    prs: List[Dict],
    stream,
) -> None:
    """For each (service, snapshot), confirm the snapshot exists in artifactory or build it."""
    for service, snapshot in deployables:
        status, _resolved, _last5 = await agents.snapshot_artifact_exists(env=None, service=service, snapshot=snapshot)
        if status == "exists":
            stream.append({"type": "log", "data": f"Snapshot {service}/{snapshot} already exists"})
            continue
        if status != "missing":
            raise RuntimeError(
                f"ensure_snapshots: unexpected artifact status {status!r} for {service}/{snapshot}"
            )
        branch_info = _branch_for_snapshot(snapshot, prs)
        if branch_info is None:
            raise RuntimeError(f"ensure_snapshots: no PR matches snapshot {snapshot} for {service}")
        repo, branch = branch_info
        stream.append({"type": "log", "data": f"building {service} from {repo}@{branch}"})
        async for event in agents.run_build(repo, branch, service=service, snapshot=snapshot):
            stream.append(event)
            if event.get("type") == "done" and not event.get("success", False):
                msg = event.get("msg", "build failed")
                raise RuntimeError(f"ensure_snapshots: build failed for {service}: {msg}")


async def deploy_snapshots(
    env: str,
    deployables: List[Tuple[str, str]],
    stream,
) -> None:
    """Deploy each (service, snapshot) pair onto the env.

    deployables: list of (service, snapshot) pairs.

    Enforces the service-cms-base-plugin rule before deploying: if the plugin is
    in the deployables list, service-cms and service-a are auto-added as concrete
    live-release snapshots. Raises RuntimeError if either can't be resolved.
    """
    deployables = await enforce_core_cms_base_plugin_rule(deployables, stream)
    for service, snapshot in deployables:
        stream.append({"type": "log", "data": f"deploying {service} @ {snapshot} onto {env}"})
        async for event in agents.run_deploy(env, service, snapshot):
            stream.append(event)
            if event.get("type") == "done" and not event.get("success", False):
                msg = event.get("msg", "deploy failed")
                raise RuntimeError(f"deploy_snapshots: deploy failed for {service}: {msg}")


async def is_env_ready_for_qa(
    ticket_key: str,
    prs: List[Dict],
) -> Tuple[bool, Optional[int]]:
    """Check whether `ticket_key`'s env exists AND every PR's snapshot is deployed.

    Returns (is_ready, expiration_unix_ts):
      - is_ready: True only if env exists, lease is live, and every deployable
        PR snapshot is the version currently running on the env (via
        agents.check_snapshot, which also verifies health).
      - expiration_unix_ts: env's `expiration` field from `deploycli ls`, or
        None if env doesn't exist / lease info is unparseable. Returned even
        when is_ready is False so the caller can decide whether a renew-only
        action is appropriate (it isn't — env must be ready first).

    Non-deployable PRs (no manifest, libraries, etc.) don't count against
    readiness: an env can be ready even if some PRs ship no service.
    """
    env_name = ticket_key.lower()

    code, out, _err = await _run(["deploycli", "deploy", "ls", env_name, "--json"])
    if code != 0:
        return False, None
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return False, None

    # `deploycli ls <env> --json` returns either a list of one env or a
    # single object. Normalize.
    if isinstance(data, list):
        if not data:
            return False, None
        env_info = data[0]
    elif isinstance(data, dict):
        env_info = data
    else:
        return False, None

    expiration = env_info.get("expiration")
    try:
        expiration_ts = int(expiration) if expiration is not None else None
    except (TypeError, ValueError):
        expiration_ts = None

    deployables, _skipped = await resolve_deployables(prs)
    if not deployables:
        # Env exists; no deployable services to verify. Ready.
        return True, expiration_ts

    for service, snapshot in deployables:
        exists, _current, _url, _info = await agents.check_snapshot(
            env_name, service, snapshot
        )
        if not exists:
            return False, expiration_ts
    return True, expiration_ts


async def provision_env(ticket_key: str, prs: List[Dict], stream) -> Dict:
    """
    Orchestrate env creation + snapshot build/deploy for a ticket.

    Returns {"status": "ok"} on success, or
            {"status": "failed", "step": str, "reason": str} on failure.
    Emits a final 'done' event on the stream either way.
    """
    env_name = ticket_key.lower()
    stream.append({"type": "stage_change", "stage": "quartermaster"})
    stream.append({"type": "log", "data": f"Provisioning env {env_name} for {ticket_key}"})

    step = "resolve_deployables"
    try:
        # Resolve deployables first (fast — Bitbucket manifest lookups) so we
        # know whether there's anything to build before we touch deploycli at all.
        deployables, skipped = await resolve_deployables(prs)
        for repo, reason in skipped:
            stream.append({"type": "log", "data": f"skipped non-deployable: {repo} ({reason})"})

        if not deployables:
            # No PR snapshots to deploy: just bring up the env and stop.
            step = "ensure_env"
            await ensure_env(env_name, stream)
            stream.append({"type": "log", "data": "No deployable services — env ready as-is"})
            stream.append({"type": "done", "status": "ok"})
            return {"status": "ok"}

        # Snapshot build and env create are independent — run them in parallel.
        # Snapshot builds typically take ~18min when a build is needed; env
        # creates take 5-10min. Running sequentially burns the difference.
        step = "ensure_snapshots+ensure_env"
        stream.append({"type": "log", "data": "Building snapshots and creating env in parallel"})
        await asyncio.gather(
            ensure_snapshots(deployables, prs, stream),
            ensure_env(env_name, stream),
        )

        step = "deploy_snapshots"
        await deploy_snapshots(env_name, deployables, stream)

        stream.append({"type": "done", "status": "ok"})
        return {"status": "ok"}
    except Exception as e:
        reason = str(e)
        stream.append({"type": "done", "status": "failed", "step": step, "msg": reason})
        return {"status": "failed", "step": step, "reason": reason}


async def renew_parent_env_lease() -> Tuple[bool, str]:
    """Renew the lease on AUTO_PROVISION_PARENT_ENV so clones can keep happening.

    The parent env is a real QA env with its own lease — if it expires,
    Deploy reclaims it and the next ticket fails at `deploycli create`
    with "parent not found". Called at backend startup and on a daily timer.

    Returns (ok, message). Caller is expected to log; this function does not
    raise."""
    code, out, err = await _run([
        "deploycli", "deploy", "renew",
        "--env", AUTO_PROVISION_PARENT_ENV,
        "--hour", str(AUTO_PROVISION_PARENT_KEEPALIVE_HOURS),
    ], timeout=_RENEW_TIMEOUT_SEC)
    if code != 0:
        return False, (err.strip() or out.strip() or f"exit code {code}")
    return True, f"renewed {AUTO_PROVISION_PARENT_ENV} for {AUTO_PROVISION_PARENT_KEEPALIVE_HOURS}h"
