import asyncio
import os
import re
import time

import httpx

import otel as _otel

from config import DEFAULT_ENV, EVIDENCE_DIR, reference_for, qa_target_host_for

BUILD_ESTIMATED_SECONDS = 20 * 60
DEPLOY_ESTIMATED_SECONDS = 25 * 60  # deploys take ~20 min
POLL_INTERVAL = 30
DEPLOY_INITIAL_WAIT = 20 * 60  # wait 20 min before first deploy check
DEPLOY_POLL_INTERVAL = 2 * 60  # then poll every 2 min
# Builds reliably take ~18 min. Start polling artifactory at t+16 min so we can
# kick off deploy the moment Jenkins publishes — saves the ~2-4 min of slack on
# every ticket. Hard cap at t+30 min before giving up.
BUILD_ARTIFACT_POLL_START_SEC = 16 * 60
BUILD_ARTIFACT_POLL_INTERVAL = 30
BUILD_ARTIFACT_MAX_SEC = 30 * 60

# Kubernetes caps label values at 63 chars; Deploy writes version as
# "<semver>-<snapshot_label>" into that label, so the snapshot portion is
# truncated mid-string once the total crosses 63. _snapshot_matches uses
# this to decide whether a prefix-match against a deployed version is
# actually a truncated snapshot vs. an unrelated one that happens to share
# a prefix.
K8S_LABEL_LIMIT = 63


async def _run_cmd_and_capture(cmd, timeout=None):
    """Run a command, capture all output lines, return (exit_code, lines).

    If `timeout` is set and the command runs longer, the process is killed
    and we return exit_code=-1 plus whatever output we captured so far.
    Without this guard `deploycli --skip-update deploy ... --status` can wedge for
    45+ min (observed 2026-05-13) and silently block the pipeline.
    """
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines = []

    async def _drain():
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode().strip()
            if text:
                lines.append(text)

    try:
        if timeout is not None:
            await asyncio.wait_for(_drain(), timeout=timeout)
        else:
            await _drain()
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        return -1, lines

    return_code = await process.wait()
    return return_code, lines


def _strip_to_json(text: str) -> str:
    """Strip ANSI codes and any leading non-JSON banner lines (e.g. deploycli login notices)."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    idx = clean.find("{")
    return clean[idx:] if idx != -1 else clean


def _extract_jenkins_url(lines):
    """Extract Jenkins build URL from deploycli command output."""
    for line in lines:
        match = re.search(r'https?://[^\s\x1b]*jenkins[^\s\x1b]*', line)
        if match:
            return re.sub(r'\x1b\[[0-9;]*m', '', match.group(0))
    return None


_TRUNK_BRANCHES = {"main", "master", "develop", "trunk"}


def _is_trunk_dest(dest_branch: str) -> bool:
    """A 'real' integration target — main/master/develop/release-* — not another feature branch."""
    if not dest_branch:
        return False
    d = dest_branch.strip().lower()
    return d in _TRUNK_BRANCHES or d.startswith("release/") or d.startswith("releases/")


def _consolidate_prs(prs):
    """Pick one PR per repo so we don't fire redundant deploys on the same service.

    Returns (kept, dropped) where each entry has {repo, branch, destBranch, prStatus, ..., reason?}.
    Rules in order:
      1. Drop DECLINED PRs.
      2. Per repo: prefer PRs whose destBranch is trunk (main/master/develop/release-*).
      3. If still >1 per repo: drop PRs whose destBranch matches another candidate's source branch
         (these are stacked on top of another listed PR — e.g. an e2e PR rebased on the feature PR).
      4. Tie-break: OPEN over MERGED, then lexicographic branch name for determinism.
    """
    kept, dropped = [], []

    for pr in prs:
        if (pr.get("prStatus") or "").upper() == "DECLINED":
            dropped.append({**pr, "reason": "DECLINED"})
        else:
            kept.append(pr)

    by_repo = {}
    for pr in kept:
        by_repo.setdefault(pr["repo"], []).append(pr)

    final = []
    for repo, group in by_repo.items():
        if len(group) == 1:
            final.append(group[0])
            continue

        trunk = [pr for pr in group if _is_trunk_dest(pr.get("destBranch", ""))]
        if trunk:
            for pr in group:
                if pr not in trunk:
                    dropped.append({**pr, "reason": f"stacked on {pr.get('destBranch') or '?'} (sibling PR targets trunk)"})
            group = trunk

        if len(group) > 1:
            source_branches = {pr["branch"] for pr in group}
            non_stacked = [pr for pr in group if pr.get("destBranch", "") not in source_branches]
            if non_stacked and len(non_stacked) < len(group):
                for pr in group:
                    if pr not in non_stacked:
                        dropped.append({**pr, "reason": f"stacked on sibling PR ({pr.get('destBranch')})"})
                group = non_stacked

        if len(group) > 1:
            group.sort(key=lambda pr: (
                0 if (pr.get("prStatus") or "").upper() == "OPEN" else 1,
                pr.get("branch", ""),
            ))
            for pr in group[1:]:
                dropped.append({**pr, "reason": "duplicate PR on same repo (kept first by OPEN/name)"})
            group = group[:1]

        final.extend(group)

    return final, dropped


async def _poll_snapshot(env, service, snapshot, estimated_seconds):
    """Poll deploycli --skip-update ls until the snapshot appears. Yields progress events.
    Final yield is a _result event."""
    start_time = time.time()
    service_name = service.split("/")[-1] if "/" in service else service

    while True:
        elapsed = time.time() - start_time
        pct = min(95, int((elapsed / estimated_seconds) * 100))
        remaining = max(0, estimated_seconds - elapsed)
        eta = f"{int(remaining / 60)} min" if remaining > 60 else f"{int(remaining)} sec"

        exists, version, url, status_info = await check_snapshot(env, service_name, snapshot)
        if exists:
            yield {"type": "log", "data": f"Snapshot ready: {version}"}
            yield {"type": "log", "data": f"URL: {url}"}
            yield {"type": "_result", "success": True, "msg": f"Done ({int(elapsed/60)} min)", "url": url}
            return
        # Detect failed deploys: version is right but health is not
        version_matches = _snapshot_matches(snapshot, version or "")
        if version_matches and status_info["status"] in ("FAILED", "UNSTABLE", "ERROR"):
            yield {"type": "log", "data": f"Deploy failed: status={status_info['status']} build={status_info['buildStatus']} scale={status_info['scaleCurrent']}/{status_info['scaleTarget']}"}
            yield {"type": "_result", "success": False, "msg": f"Deploy failed ({status_info['status']})", "url": url}
            return

        yield {"type": "progress", "pct": pct, "eta": eta}
        status_hint = f" [{status_info['status']} {status_info['scaleCurrent']}/{status_info['scaleTarget']}]" if status_info["status"] else ""
        yield {"type": "log", "data": f"Waiting for snapshot... {int(elapsed/60)} min ({version or 'not found'}){status_hint}"}

        if elapsed > 45 * 60:
            yield {"type": "_result", "success": False, "msg": "Timed out after 45 minutes"}
            return

        await asyncio.sleep(POLL_INTERVAL)


async def _poll_deploy(env, service, snapshot, timeout=45*60):
    """Poll with 20min initial wait then 2min intervals. Returns (success, msg, url)."""
    service_name = service.split("/")[-1] if "/" in service else service
    start = time.time()
    # Wait 20 min before first check
    await asyncio.sleep(DEPLOY_INITIAL_WAIT)
    while True:
        elapsed = time.time() - start
        exists, version, url, status_info = await check_snapshot(env, service_name, snapshot)
        if exists:
            return True, f"{service_name} deployed ({int(elapsed/60)} min)", url
        version_matches = _snapshot_matches(snapshot, version or "")
        if version_matches and status_info["status"] in ("FAILED", "UNSTABLE", "ERROR"):
            return False, f"{service_name} deploy failed: status={status_info['status']} build={status_info['buildStatus']} scale={status_info['scaleCurrent']}/{status_info['scaleTarget']}", url
        if elapsed > timeout:
            return False, f"{service_name} timed out after {int(elapsed/60)} min", ""
        await asyncio.sleep(DEPLOY_POLL_INTERVAL)


async def _deploy_service(env, service, snapshot):
    """Trigger deploy and poll until live. Returns (success, service, msg, url, logs)."""
    service_name = service.split("/")[-1] if "/" in service else service
    cmd = f"deploycli --skip-update deploy {env}/{service_name} --snapshot {snapshot} -y"
    logs = [f"Deploying {snapshot} to {env}/{service_name}"]
    code, lines = await _run_cmd_and_capture(cmd)
    logs.extend(lines)
    if code != 0:
        return False, service_name, f"Deploy trigger failed (exit {code})", "", logs
    success, msg, url = await _poll_deploy(env, service_name, snapshot)
    logs.append(msg)
    return success, service_name, msg, url, logs


async def _reset_service(env, service):
    """Reset a service to its stable reference. Returns (success, service, logs)."""
    ref = reference_for(service)
    cmd = f"deploycli --skip-update deploy {env}/{service} --reference {ref} -y"
    logs = [f"Resetting {service} to {ref}"]
    code, lines = await _run_cmd_and_capture(cmd)
    logs.extend(lines)
    if code == 0:
        logs.append(f"{service} reset to {ref}")
    else:
        logs.append(f"Warning: failed to reset {service}")
    return code == 0, service, logs


async def cleanup_env(env_name: str, keep_services: list = None):
    """Reset every snapshot service on env_name to its stable reference,
    except services in keep_services (current ticket's services).

    Yields SSE-style events: {type: log|progress|done, ...}
    """
    keep = set(keep_services or [])
    yield {"type": "log", "data": f"Inspecting {env_name}..."}

    import json as _json
    # --skip-update guards against the case where deploycli's auto-update probe
    # swallows the JSON output, yielding exit 0 + empty stdout. Bounded
    # timeout so a hung deploycli CLI doesn't wedge the whole pipeline (we lost
    # 45 min to this exact pattern on 2026-05-13).
    code, lines = await _run_cmd_and_capture(
        f"deploycli --skip-update ls {env_name} --json",
        timeout=60,
    )
    if code != 0:
        yield {"type": "log", "data": f"Failed to list env: exit {code}"}
        if lines:
            for line in lines[-5:]:
                yield {"type": "log", "data": f"  {line}"}
        yield {"type": "done", "success": False, "msg": "list failed"}
        return

    raw = _strip_to_json("\n".join(lines))
    if not raw:
        # exit 0 + no output is the silent-failure mode we want to surface
        # loudly. Tell the user what to do instead of "parse failed".
        yield {"type": "log", "data": "deploycli returned exit 0 with no output — likely an auth refresh or update check ate the response."}
        yield {"type": "log", "data": "Retry the cleanup. If it keeps happening, run `deploycli --skip-update ls " + env_name + " --json` in a terminal to see what deploycli is actually saying."}
        yield {"type": "done", "success": False, "msg": "empty output from deploycli"}
        return

    try:
        env_data = _json.loads(raw)
    except Exception as e:
        yield {"type": "log", "data": f"Failed to parse env JSON: {e}"}
        yield {"type": "log", "data": f"First 200 chars of output: {raw[:200]!r}"}
        yield {"type": "done", "success": False, "msg": "parse failed"}
        return

    snapshot_services = []
    for svc_data in env_data.get("services", []):
        name = svc_data.get("name", "")
        version = svc_data.get("version", "")
        svc_type = svc_data.get("type", "")
        is_reference = svc_type == "reference" and "stable" in version.lower()
        if not is_reference and name not in keep:
            snapshot_services.append({"name": name, "version": version, "type": svc_type})

    if not snapshot_services:
        yield {"type": "log", "data": "Env already clean — nothing to reset."}
        yield {"type": "done", "success": True, "reset": [], "kept": list(keep)}
        return

    yield {"type": "log", "data": f"{len(snapshot_services)} service(s) to reset:"}
    for s in snapshot_services:
        yield {"type": "log", "data": f"  {s['name']}: {s['version']} -> {reference_for(s['name'])}"}

    reset_results = []
    total = len(snapshot_services)
    for i, s in enumerate(snapshot_services):
        ok, _, logs = await _reset_service(env_name, s["name"])
        for line in logs:
            yield {"type": "log", "data": f"  {line}"}
        reset_results.append({"service": s["name"], "ok": ok})
        yield {"type": "progress", "pct": int(((i + 1) / total) * 100), "eta": ""}

    failed = [r for r in reset_results if not r["ok"]]
    if failed:
        yield {"type": "log", "data": f"Done with {len(failed)} failure(s)."}
    else:
        yield {"type": "log", "data": "All resets triggered successfully."}
    yield {
        "type": "done",
        "success": len(failed) == 0,
        "reset": reset_results,
        "kept": list(keep),
    }


def _snapshot_matches(expected_snapshot: str, current_version: str) -> bool:
    """Match an expected snapshot label against a deployed version string.

    `current_version` looks like `3.403.0-FEATURE-PROJ-311-...-SCHEDUL` — a
    leading semver-ish prefix, then the snapshot label, possibly truncated
    by K8s' 63-char label limit. `expected_snapshot` is the full label
    derived from the branch name.

    Accepts:
      1. The full expected label appears verbatim anywhere in the deployed version.
      2. After stripping the leading semver, the deployed label equals the expected.
      3. Truncation: the deployed label is a prefix of the expected, AND that
         truncation matches K8s' physical limit (total version length ==
         K8S_LABEL_LIMIT, with the expected label longer than the available
         budget).

    Rejects loose prefix matches — two long branches that happen to share a
    leading segment but were never truncated at the K8s boundary are NOT
    the same snapshot.
    """
    if not expected_snapshot or not current_version:
        return False
    exp = expected_snapshot.upper()
    cur = current_version.upper()
    # Case 1: the full expected label appears in the deployed version.
    if exp in cur:
        return True
    # Strip the leading "<num>(.<num>)*-" semver to isolate the deployed
    # snapshot label.
    deployed_label = re.sub(r'^\d+(?:\.\d+)*-', '', cur)
    if not deployed_label:
        return False
    # Case 2: exact label match after stripping semver.
    if deployed_label == exp:
        return True
    # Case 3: K8s truncation. The deployed label must be a prefix of the
    # expected, AND truncation must actually be the explanation: the full
    # version must be at the K8s limit, and the expected must be longer
    # than what fits.
    semver_prefix_len = len(cur) - len(deployed_label)
    available_for_label = K8S_LABEL_LIMIT - semver_prefix_len
    if available_for_label <= 0:
        return False
    return (
        len(cur) == K8S_LABEL_LIMIT
        and len(exp) > available_for_label
        and len(deployed_label) == available_for_label
        and exp.startswith(deployed_label)
    )


SNAPSHOT_STATUS_TIMEOUT = 60  # seconds; `deploycli deploy --status` has been seen to hang.


async def snapshot_artifact_exists(env, service, snapshot):
    """Check whether a snapshot artifact exists for `service` WITHOUT deploying it.

    Uses `deploycli --skip-update deploy ... --status` which is read-only. On success it
    prints "Found N snapshot matches" and resolves the version; on failure it
    raises an InputError and exits non-zero, listing the last 5 snapshots so we
    can surface those in logs.

    Returns (status, resolved_version, last_5_snapshots) where status is one of:
      "exists"   — snapshot found, safe to deploy
      "missing"  — confirmed not built; last5 has recent snapshots for context
      "timeout"  — `deploycli --status` hung past SNAPSHOT_STATUS_TIMEOUT; caller decides
    """
    env_name = env or DEFAULT_ENV
    service_name = service.split("/")[-1] if "/" in service else service
    cmd = f"deploycli --skip-update deploy {env_name}/{service_name} --snapshot {snapshot} --status"
    code, lines = await _run_cmd_and_capture(cmd, timeout=SNAPSHOT_STATUS_TIMEOUT)
    text = "\n".join(lines)
    clean = re.sub(r'\x1b\[[0-9;]*m', '', text)
    if code == -1:
        return "timeout", "", []
    artifact_match = re.search(r'Using first found:\s*(\S+)', clean)
    if code == 0:
        resolved = artifact_match.group(1) if artifact_match else ""
        return "exists", resolved, []
    # Artifact resolution prints "Using first found" before any env-resolution step,
    # so if it appears even on a non-zero exit (e.g. env doesn't exist yet) the
    # artifact itself is present and safe to deploy once the env is ready.
    if artifact_match:
        return "exists", artifact_match.group(1), []
    # Pull the "Last 5 snapshots" block for the log so the user can see what's available.
    last5 = []
    if "Last 5 snapshots" in clean:
        capture = False
        for raw in clean.splitlines():
            line = raw.strip()
            if "Last 5 snapshots" in line:
                capture = True
                continue
            if not capture:
                continue
            if not line or line.startswith(("at ", "throw ", "^", "error:")):
                break
            last5.append(line)
            if len(last5) >= 5:
                break
    return "missing", "", last5


async def check_snapshot(env, service, snapshot):
    """Check if a snapshot is already deployed and healthy.
    Returns (exists, current_version, url, status_info).
    status_info = {"status": "STABLE|DEPLOYING|FAILED|...", "buildStatus": "SUCCESS|FAILURE|...",
                   "scaleCurrent": int, "scaleTarget": int, "healthy": bool}.
    `exists` is True only when the right snapshot version is on the service AND it looks healthy
    (status STABLE, buildStatus SUCCESS, scale.current >= scale.target and > 0)."""
    import json as _json
    service_name = service.split("/")[-1] if "/" in service else service
    env_name = env or DEFAULT_ENV
    cmd = f"deploycli --skip-update ls {env_name} {service_name} --json"
    empty_status = {"status": "", "buildStatus": "", "scaleCurrent": 0, "scaleTarget": 0, "healthy": False, "buildUrl": ""}
    try:
        return_code, lines = await _run_cmd_and_capture(cmd)
        if return_code != 0:
            return False, "", "", empty_status
        # Parse JSON output
        raw = _strip_to_json("\n".join(lines))
        data = _json.loads(raw)
        current_version = data.get("version", "")
        # URL can be a JSON string with urls array, or a plain string
        url_field = data.get("url", "")
        if isinstance(url_field, str) and url_field.startswith("{"):
            try:
                url_data = _json.loads(url_field)
                url = url_data.get("urls", [""])[0]
            except Exception:
                url = url_field
        else:
            url = url_field
        status = (data.get("status") or "").upper()
        build_status = (data.get("buildStatus") or "").upper()
        scale = data.get("scale") or {}
        scale_current = int(scale.get("current") or 0)
        scale_target = int(scale.get("target") or 0)
        # deploycli ls <env> <service> --json returns status+scale only from full env listings.
        # Per-service queries often omit both fields entirely. Accept as healthy when:
        #   - status is STABLE (full env listing), OR
        #   - status is absent but buildStatus is SUCCESS (per-service listing)
        # In both cases the version must match (checked below via _snapshot_matches).
        has_scale_info = scale_target > 0
        healthy = build_status not in ("FAILURE", "ABORTED") and (
            (status == "STABLE" and has_scale_info and scale_current >= scale_target)
            or (status in ("STABLE", "") and build_status == "SUCCESS")
        )
        build_url = data.get("build", "")
        status_info = {
            "status": status,
            "buildStatus": build_status,
            "scaleCurrent": scale_current,
            "scaleTarget": scale_target,
            "healthy": healthy,
            "buildUrl": build_url,
        }
        version_matches = _snapshot_matches(snapshot, current_version)
        return (version_matches and healthy), current_version, url, status_info
    except Exception:
        pass
    return False, "", "", {"status": "", "buildStatus": "", "scaleCurrent": 0, "scaleTarget": 0, "healthy": False, "buildUrl": ""}


async def run_build(repo, branch, service=None, snapshot=None):
    """Trigger build and wait for completion.

    If `service` and `snapshot` are provided, after t+16 min we poll artifactory
    every 30s and return success the instant the artifact exists — this lets
    `ensure_snapshots` hand off to `deploy_snapshots` without the ~2-4 min of
    slack baked into the blind 20-min sleep. Hard cap at t+30 min.

    Without those args, falls back to the legacy blind time-based wait — kept
    for callers that don't know the snapshot label up front."""
    full_repo = repo if "/" in repo else f"acme/{repo}"
    cmd = f"deploycli --skip-update build -r {full_repo} -b {branch}"
    yield {"type": "log", "data": f"Starting Build: {cmd}"}

    return_code, lines = await _run_cmd_and_capture(cmd)
    for line in lines:
        yield {"type": "log", "data": line}

    if return_code != 0:
        yield {"type": "done", "success": False, "msg": f"Build trigger failed (exit code {return_code})"}
        return

    can_poll_artifact = bool(service and snapshot)
    if can_poll_artifact:
        yield {"type": "log", "data": f"Build triggered on Jenkins. Watching for artifact (poll starts at t+{BUILD_ARTIFACT_POLL_START_SEC // 60} min)..."}
    else:
        yield {"type": "log", "data": f"Build triggered on Jenkins. Waiting ~{BUILD_ESTIMATED_SECONDS // 60} min..."}

    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if can_poll_artifact and elapsed >= BUILD_ARTIFACT_POLL_START_SEC:
            status, resolved, _last5 = await snapshot_artifact_exists(None, service, snapshot)
            if status == "exists":
                yield {"type": "log", "data": f"Artifact ready: {resolved or snapshot} (at t+{int(elapsed/60)} min)"}
                yield {"type": "progress", "pct": 100, "eta": ""}
                yield {"type": "done", "success": True}
                return
            if elapsed >= BUILD_ARTIFACT_MAX_SEC:
                yield {"type": "done", "success": False, "msg": f"Build timed out: artifact {snapshot} not in artifactory after {int(elapsed/60)} min"}
                return
            pct = min(95, int((elapsed / BUILD_ARTIFACT_MAX_SEC) * 100))
            remaining = max(0, BUILD_ARTIFACT_MAX_SEC - elapsed)
            eta = f"{int(remaining / 60)} min" if remaining > 60 else f"{int(remaining)} sec"
            yield {"type": "progress", "pct": pct, "eta": eta}
            yield {"type": "log", "data": f"Polling artifactory... (t+{int(elapsed/60)} min)"}
            await asyncio.sleep(BUILD_ARTIFACT_POLL_INTERVAL)
            continue
        if elapsed >= BUILD_ESTIMATED_SECONDS:
            break
        pct = min(95, int((elapsed / BUILD_ESTIMATED_SECONDS) * 100))
        remaining = max(0, BUILD_ESTIMATED_SECONDS - elapsed)
        eta = f"{int(remaining / 60)} min" if remaining > 60 else f"{int(remaining)} sec"
        yield {"type": "progress", "pct": pct, "eta": eta}
        yield {"type": "log", "data": f"Building... ({int(elapsed/60)} min elapsed)"}
        await asyncio.sleep(POLL_INTERVAL)

    yield {"type": "progress", "pct": 100, "eta": ""}
    yield {"type": "done", "success": True}


async def run_deploy(env, service, snapshot):
    """Trigger deploy, then poll deploycli --skip-update ls to confirm snapshot is live."""
    env_name = env or DEFAULT_ENV
    service_name = service.split("/")[-1] if "/" in service else service
    cmd = f"deploycli --skip-update deploy {env_name}/{service_name} --snapshot {snapshot}"
    yield {"type": "log", "data": f"Starting Deploy: {cmd}"}

    return_code, lines = await _run_cmd_and_capture(cmd)
    for line in lines:
        yield {"type": "log", "data": line}

    if return_code != 0:
        yield {"type": "done", "success": False, "msg": f"Deploy trigger failed (exit code {return_code})"}
        return

    # Poll env to confirm snapshot is deployed
    yield {"type": "log", "data": f"Deploy triggered. Polling {env_name}/{service_name} for {snapshot}..."}
    yield {"type": "progress", "pct": 5, "eta": f"~{DEPLOY_ESTIMATED_SECONDS // 60} min"}

    async for event in _poll_snapshot(env_name, service_name, snapshot, DEPLOY_ESTIMATED_SECONDS):
        if event.get("type") == "_result":
            if event["success"]:
                yield {"type": "log", "data": event["msg"]}
                yield {"type": "progress", "pct": 100, "eta": ""}
                yield {"type": "done", "success": True}
            else:
                yield {"type": "done", "success": False, "msg": event["msg"]}
            return
        else:
            yield event


async def run_test(ticket_key, env_url):
    """Emit the QA command for the user and finish. User clicks 'Check Evidence' when ready."""
    from instance_config import load_instance_config
    _cfg = load_instance_config() or {}
    skill_cmd = _cfg.get("skillCommand") or "/qa-evidence"
    # Build via the server-side runner's helper so the copy-paste command can't
    # drift from it again — it was missing --isolated, which left runs blocked on
    # "browser already in use". Lazy import avoids the agents<->qa_runner cycle.
    from qa_runner import build_qa_command
    qa_cmd = build_qa_command(ticket_key, env_url, skill_cmd)

    # Record what evidence exists NOW so check_new_evidence can compare
    runs_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs")
    existing_runs = set()
    if os.path.isdir(runs_path):
        existing_runs = set(os.listdir(runs_path))

    yield {"type": "log", "data": f"Ready to test {ticket_key}"}
    if existing_runs:
        yield {"type": "log", "data": f"Existing runs: {len(existing_runs)} (will ignore these)"}
    yield {"type": "log", "data": ""}
    yield {"type": "log", "data": "Paste this in Claude Code:"}
    yield {"type": "log", "data": f"  {qa_cmd}"}
    yield {"type": "log", "data": ""}
    yield {"type": "log", "data": "Click 'Check Evidence' when tests are complete."}
    yield {"type": "progress", "pct": 10, "eta": "waiting for test run"}

    # Store baseline for later comparison
    yield {"type": "inspector_ready", "data": qa_cmd, "baseline_runs": list(existing_runs)}
    yield {"type": "done", "success": True, "waiting_for_evidence": True}


def _run_has_content(run_path):
    """Check if a run directory has meaningful content (not just empty dirs)."""
    for sub in ("automated", "manual", "markup", "diffs"):
        sub_path = os.path.join(run_path, sub)
        if os.path.isdir(sub_path) and os.listdir(sub_path):
            return True
    # Also count headless.log or summary.json as content
    for f in ("headless.log", "summary.json", "index.html"):
        if os.path.isfile(os.path.join(run_path, f)):
            return True
    return False


def check_new_evidence(ticket_key, baseline_runs=None):
    """Check if NEW evidence exists since baseline. Returns evidence dict + found flag."""
    runs_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs")
    baseline = set(baseline_runs or [])

    if not os.path.isdir(runs_path):
        return {"found": False, "evidence": check_evidence(ticket_key)}

    # Filter to directories only — `*-evidence.zip` artifacts sit next to run
    # dirs in runs/, and the zip name sorts above the bare dir, so it would
    # otherwise be picked as "latest" and yield a phantom in-progress lane.
    current_runs = {
        name for name in os.listdir(runs_path)
        if os.path.isdir(os.path.join(runs_path, name))
    }
    new_runs = current_runs - baseline

    if not new_runs:
        # No new runs — also check existing runs for content (baseline may be empty)
        if baseline_runs is not None and len(baseline_runs) == 0 and current_runs:
            # No baseline stored (old pipeline), check all runs
            new_runs = current_runs
        else:
            return {"found": False, "evidence": check_evidence(ticket_key)}

    latest_run = sorted(new_runs, reverse=True)[0]
    latest_path = os.path.join(runs_path, latest_run)

    # Auto-generate (or regenerate) index.html when:
    #   - run has content but no portal yet, OR
    #   - summary.json was written after index.html (report was built before verdicts arrived)
    index_path = os.path.join(latest_path, "index.html")
    summary_path_check = os.path.join(latest_path, "summary.json")
    summary_newer = (
        os.path.exists(summary_path_check)
        and os.path.exists(index_path)
        and os.path.getmtime(summary_path_check) > os.path.getmtime(index_path)
    )
    # Also regenerate when the skill left a thin (image-less) report next to
    # screenshots — the agent writes summary.json THEN its own index.html, so
    # summary_newer is usually False and the image-rich report never won.
    needs_regen = (
        not os.path.exists(index_path)
        or summary_newer
        or _report_missing_screenshots(latest_path)
        or _report_status_stale(latest_path)
    )
    if _run_has_content(latest_path) and needs_regen:
        generate_html_report(ticket_key, latest_run)

    has_html = os.path.exists(os.path.join(latest_path, "index.html"))
    # Fall back through summary.json → headless.log so partial/aborted runs
    # still get a clickable target in the lane card.
    report_url = _report_url_for(ticket_key, latest_run, latest_path)

    # Check summary.json first (best signal)
    summary_path = os.path.join(latest_path, "summary.json")
    if os.path.exists(summary_path):
        import json as _json
        with open(summary_path) as f:
            summary = _json.load(f)
        _raw_conf = summary.get("confidence")
        _score = summary.get("score")
        if isinstance(_score, dict):
            _pct = _score.get("pct")
            _score = round(_pct) if isinstance(_pct, (int, float)) else None
        if _score is None and isinstance(_raw_conf, dict):
            _score = _raw_conf.get("headline")
        elif _score is None and isinstance(_raw_conf, (int, float)):
            _score = _raw_conf
        return {
            "found": True,
            "run": latest_run,
            "score": _score,
            "time": summary.get("time", "") or summary.get("date", ""),
            "reportUrl": report_url,
            "evidence": check_evidence(ticket_key),
        }

    # No summary.json — check if run has actual content (screenshots, logs, etc.)
    if _run_has_content(latest_path):
        return {
            "found": True,
            "run": latest_run,
            "score": None,
            "time": "",
            "reportUrl": report_url,
            "evidence": check_evidence(ticket_key),
        }

    # Run directory exists but is empty/in-progress
    return {"found": False, "in_progress": latest_run, "evidence": check_evidence(ticket_key)}


async def _resolve_test_env_url(env, services):
    """Pick the env URL QA should target given the set of deployed services.

    CMS plugins (service-cms-base-plugin, service-cms-plugin-b, …) deploy as their own
    Deploy services but expose nothing testable on their own host — they get
    loaded into the host app (service-cms) at runtime. qa_target_host_for redirects each
    plugin service to its host app so QA exercises the real integration. For
    non-plugin services the redirect is a no-op (host == service)."""
    seen = set()
    for svc in services:
        host = qa_target_host_for(svc)
        if host in seen:
            continue
        seen.add(host)
        _, _, url, _ = await check_snapshot(env, host, "")
        if url:
            return url
    return ""


PLUGIN_TRIGGER_SERVICE = "service-cms-base-plugin"
PLUGIN_REQUIRED_CONCRETE_DEPS = ("service-cms", "service-a")
LIVE_RELEASE_SOURCE_ENV = "k8s-stable"


async def _resolve_live_release_snapshot_label(service):
    """Read what's currently deployed on k8s-stable for `service` and return
    just the snapshot label (the bit after the leading `<semver>-`).

    k8s-stable is the live release reference by definition. Returns None when
    the lookup fails, the JSON is unparseable, or the version string has no
    semver-prefixed snapshot label (e.g. pure reference-only deploys)."""
    import json as _json
    cmd = f"deploycli --skip-update ls {LIVE_RELEASE_SOURCE_ENV} {service} --json"
    code, lines = await _run_cmd_and_capture(cmd, timeout=60)
    if code != 0:
        return None
    raw = _strip_to_json("\n".join(lines))
    if not raw:
        return None
    try:
        data = _json.loads(raw)
    except Exception:
        return None
    version = (data.get("version") or "").strip()
    if not version:
        return None
    label = re.sub(r"^\d+(?:\.\d+)*-", "", version)
    if not label or label == version:
        return None
    return label


async def _enforce_plugin_concrete_deps(services):
    """If service-cms-base-plugin is in `services`, ensure service-cms AND service-a are
    also in `services` as concrete live-release snapshot deploys. Fails closed:
    if either can't be resolved from k8s-stable, raise RuntimeError so the
    caller marks the stage failed.

    `services` is a list of dicts shaped like {"service", "snapshot", ...}.
    Returns the (possibly expanded) list. Trigger service absent → no-op.
    """
    present = {s.get("service") for s in services}
    if PLUGIN_TRIGGER_SERVICE not in present:
        return services
    expanded = list(services)
    for dep in PLUGIN_REQUIRED_CONCRETE_DEPS:
        if dep in present:
            # PR snapshot wins — the ticket explicitly changes this service.
            continue
        snap = await _resolve_live_release_snapshot_label(dep)
        if not snap:
            raise RuntimeError(
                f"Cannot determine live-release snapshot for {dep}; refusing "
                f"to deploy {PLUGIN_TRIGGER_SERVICE} without concrete deps. "
                f"Manually deploy a concrete {dep} snapshot and retry."
            )
        expanded.append({"service": dep, "snapshot": snap})
    return expanded


async def _builder_stage(env, services, check_deploy_fn=None):
    """Builder stage as an async generator.

    If all snapshots are already deployed (e.g. Quartermaster pre-staged them),
    emit a log and complete immediately without triggering deploycli build.
    Otherwise fall through to build each service that lacks a snapshot artifact.

    Enforces the service-cms-base-plugin rule: when the plugin is in `services`,
    service-cms AND service-a are appended as concrete live-release snapshot deploys
    (resolved from k8s-stable) BEFORE the fast-path check. Fails closed if
    either dep's live-release snapshot can't be resolved.

    Args:
        env: Deploy environment name (e.g. "qa-env").
        services: List of dicts with at minimum {"service", "snapshot"}.
                  The fall-through build path also reads "repo" and "branch".
        check_deploy_fn: Async callable (env, services) -> {"allDeployed": bool, ...}.
                         Defaults to server._check_deploy_core when None.
    """
    if check_deploy_fn is None:
        from server import _check_deploy_core
        check_deploy_fn = _check_deploy_core

    yield {"type": "stage_change", "stage": "builder"}

    # Concrete-deps rule must fire before the fast-path check so the check
    # operates on the full required deploy set, not just the ticket's PRs.
    try:
        services = await _enforce_plugin_concrete_deps(services)
    except RuntimeError as e:
        yield {"type": "log", "data": str(e)}
        yield {"type": "done", "status": "error"}
        return

    try:
        check = await check_deploy_fn(env, services)
    except Exception as e:
        yield {"type": "log", "data": f"Builder fast-path check failed: {e}"}
        yield {"type": "done", "status": "error"}
        return
    if check.get("allDeployed"):
        yield {"type": "log", "data": "Snapshots already staged by Quartermaster — skipping build+deploy"}
        yield {"type": "done", "status": "ok"}
        return

    # Fall through to existing build+deploy behavior
    for svc in services:
        repo = svc.get("repo")
        branch = svc.get("branch")
        if not repo or not branch:
            yield {"type": "log", "data": f"Skipping {svc.get('service', '?')}: missing repo or branch"}
            continue
        async for event in run_build(repo, branch, service=svc.get("service"), snapshot=svc.get("snapshot")):
            yield event
            # Note: _builder_stage uses status-shape; run_build's success-shape would need translation if wired through.
            if event.get("type") == "done" and event.get("status") != "ok":
                yield {"type": "done", "status": "error"}
                return

    # (Deploy then happens in the next stage / existing logic — leave the
    # caller responsible for invoking Shipper.)
    yield {"type": "done", "status": "ok"}


async def run_pipeline(repo, branch, env, service, snapshot, ticket_key, env_url):
    """Full pipeline: fetch dev info → build all repos → deploy all → test → evidence."""
    env_name = env or DEFAULT_ENV

    # Already-deployed apps (static/local/deployed modes) skip build & deploy entirely:
    # we just need the PR (for analysis) + ticket info, then emit the test command.
    from instance_config import load_instance_config
    _cfg = load_instance_config() or {}
    _mode = (_cfg.get("environments") or {}).get("mode")
    if _mode in ("static", "local", "deployed"):
        if not env_url:
            _urls = (_cfg.get("environments") or {}).get("staticUrls") or []
            env_url = (_urls[0] if _urls else None) or env
        yield {"type": "log", "data": f"=== Pipeline for {ticket_key} ==="}
        yield {"type": "log", "data": f"App is already deployed ({_mode}) — skipping build & deploy."}
        yield {"type": "stage_change", "stage": "inspector"}
        yield {"type": "log", "data": f"[Inspector] Target env: {env_url or 'not set'}"}
        async for event in run_test(ticket_key, env_url):
            yield event
        return

    # Fetch dev info from Jira to discover all PRs/repos
    from jira_client import _get_dev_info, _headers
    import httpx as _httpx

    yield {"type": "log", "data": f"=== Pipeline for {ticket_key} ==="}
    yield {"type": "log", "data": f"Env: {env_name}"}
    yield {"type": "log", "data": ""}

    # Get issue ID then dev info
    yield {"type": "log", "data": "Fetching dev info from Jira..."}
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://acme.atlassian.net/rest/api/2/issue/{ticket_key}",
                params={"fields": "id"},
                headers=_headers(),
            )
            issue_id = resp.json()["id"] if resp.status_code == 200 else None
        dev_info = await _get_dev_info(issue_id) if issue_id else []
    except Exception:
        dev_info = []

    # Fall back to the single repo/branch passed in
    if not dev_info:
        if repo and branch:
            dev_info = [{"repo": repo, "branch": branch, "prStatus": ""}]
        else:
            yield {"type": "done", "success": False, "msg": "No dev info found in Jira"}
            return

    # Filter out PRs whose branch doesn't reference this ticket — Jira's dev-info
    # often links unrelated PRs to a ticket, which dragged Shipper into deploying
    # the wrong service.
    if ticket_key:
        key_upper = ticket_key.upper()
        filtered = [pr for pr in dev_info if key_upper in (pr.get("branch") or "").upper()]
        dropped = [pr for pr in dev_info if pr not in filtered]
        for pr in dropped:
            yield {"type": "log", "data": f"Ignoring PR with unrelated branch: {pr['repo']} @ {pr['branch']}"}
        if filtered:
            dev_info = filtered
        else:
            yield {"type": "log", "data": f"Warning: no PR branches reference {ticket_key} — proceeding with all linked PRs"}

    # PR analysis: collapse multiple PRs targeting the same service to one snapshot.
    # Without this, two open PRs on service-cms (feature + stacked e2e) would each
    # trigger a deploy and overwrite each other — wasting ~40 min and shipping the
    # wrong snapshot.
    yield {"type": "log", "data": "Analyzing PRs..."}
    for pr in dev_info:
        yield {"type": "log", "data": f"  {pr['repo']} @ {pr['branch']} → {pr.get('destBranch') or '?'} [{pr.get('prStatus') or '?'}]"}
    consolidated, dropped_prs = _consolidate_prs(dev_info)
    for pr in dropped_prs:
        yield {"type": "log", "data": f"  Dropping {pr['repo']} @ {pr['branch']}: {pr.get('reason', 'redundant')}"}
    if not consolidated:
        yield {"type": "done", "success": False, "msg": "No deployable PRs after analysis (all declined or filtered)"}
        return
    dev_info = consolidated

    # Summary of what we found
    yield {"type": "log", "data": f"Will deploy {len(dev_info)} snapshot(s):"}
    for i, pr in enumerate(dev_info):
        svc = pr["repo"].split("/")[-1] if "/" in pr["repo"] else pr["repo"]
        snap = pr["branch"].upper().replace("/", "-")
        yield {"type": "log", "data": f"  {i+1}. {pr['repo']} @ {pr['branch']}"}
        yield {"type": "log", "data": f"     Snapshot: {snap} | Service: {svc}"}
    yield {"type": "log", "data": ""}

    # ── Step 1: Cleanup — always reset unrelated snapshots before we touch the env ──
    # Single source of truth: delegate to cleanup_env() (the same generator the
    # explicit "Clean Env" button uses). Runs unconditionally so a stale snapshot
    # from another ticket can never coexist with this ticket's deploys.
    ticket_snapshots = {}  # service_name -> snapshot_name
    for pr in dev_info:
        svc = pr["repo"].split("/")[-1] if "/" in pr["repo"] else pr["repo"]
        snap = pr["branch"].upper().replace("/", "-")
        ticket_snapshots[svc] = snap

    keep = list(ticket_snapshots.keys())
    yield {"type": "log", "data": f"[Cleanup] Resetting unrelated snapshots on {env_name} (keeping: {', '.join(keep) or 'none'})"}
    async for event in cleanup_env(env_name, keep_services=keep):
        etype = event.get("type")
        if etype == "done":
            if not event.get("success", False):
                yield {"type": "log", "data": "[Cleanup] Some resets failed — continuing; deploys may collide."}
            continue
        if etype == "progress":
            # Swallow cleanup progress so it doesn't fight with later stage progress bars.
            continue
        if etype == "log":
            yield {"type": "log", "data": f"[Cleanup] {event.get('data', '')}"}
        else:
            yield event
    yield {"type": "log", "data": ""}

    # Early exit: every required snapshot is already deployed and healthy on this env.
    # No Builder, no Shipper, no redundant deploy attempts. (Cleanup above already
    # reset any unrelated snapshots, so we don't need a stale-services gate here.)
    all_healthy = bool(ticket_snapshots)
    healthy_urls = {}
    for svc, snap in ticket_snapshots.items():
        ok, ver, u, _ = await check_snapshot(env_name, svc, snap)
        if ok:
            healthy_urls[svc] = u
        else:
            all_healthy = False
            break
    if all_healthy:
        yield {"type": "log", "data": f"All required snapshots already deployed and healthy on {env_name} — skipping Builder/Shipper"}
        yield {"type": "stage_change", "stage": "builder"}
        yield {"type": "progress", "pct": 100, "eta": ""}
        yield {"type": "stage_change", "stage": "shipper"}
        yield {"type": "progress", "pct": 100, "eta": ""}
        if not env_url:
            env_url = await _resolve_test_env_url(env_name, list(ticket_snapshots.keys())) or env_url
        yield {"type": "stage_change", "stage": "inspector"}
        yield {"type": "log", "data": f"[Inspector] Env URL: {env_url or 'not detected'}"}
        async for event in run_test(ticket_key, env_url):
            yield event
        return

    # ── Step 2: Builder — check snapshots, build if needed ──
    yield {"type": "stage_change", "stage": "builder"}

    needs_deploy = []  # {repo, branch, service, snapshot, skip}

    for i, pr in enumerate(dev_info):
        pr_repo = pr["repo"]
        pr_branch = pr["branch"]
        svc = pr_repo.split("/")[-1] if "/" in pr_repo else pr_repo
        snap = pr_branch.upper().replace("/", "-")

        yield {"type": "log", "data": f"[Builder {i+1}/{len(dev_info)}] {svc}"}

        # Check if already deployed on this env
        deployed, current_version, url, status_info = await check_snapshot(env_name, svc, snap)

        if deployed:
            yield {"type": "log", "data": f"  Already deployed (healthy): {current_version}"}
            yield {"type": "log", "data": f"  URL: {url}"}
            needs_deploy.append({"repo": pr_repo, "branch": pr_branch, "service": svc, "snapshot": snap, "skip": True})
        else:
            version_matches = _snapshot_matches(snap, current_version or "")
            if version_matches and status_info["status"] in ("FAILED", "UNSTABLE", "ERROR"):
                yield {"type": "log", "data": f"  Snapshot deployed but UNHEALTHY: status={status_info['status']} build={status_info['buildStatus']} scale={status_info['scaleCurrent']}/{status_info['scaleTarget']} - will redeploy"}
            elif current_version:
                yield {"type": "log", "data": f"  Current: {current_version} (need: {snap})"}

            # Read-only check: does the snapshot artifact exist? (no deploy side-effect)
            # Builder NEVER issues `deploycli --skip-update deploy --snapshot` — Shipper is the
            # single source of truth. Issuing the deploy in both stages would
            # restart the rollout twice and leave the env stuck on the reference.
            yield {"type": "log", "data": f"  Checking if snapshot {snap} exists for {svc}..."}
            status, resolved, last5 = await snapshot_artifact_exists(env_name, svc, snap)

            if status == "exists":
                yield {"type": "log", "data": f"  Snapshot exists ({resolved or snap}). Shipper will deploy it."}
                needs_deploy.append({"repo": pr_repo, "branch": pr_branch, "service": svc, "snapshot": snap, "skip": False})
            elif status == "missing":
                # Snapshot not built yet. Show recent snapshots in case it's a typo / stale branch.
                yield {"type": "log", "data": f"  Snapshot {snap} not found. Building {pr_repo} @ {pr_branch}..."}
                if last5:
                    yield {"type": "log", "data": f"  Recent snapshots for {svc}:"}
                    for s in last5:
                        yield {"type": "log", "data": f"    {s}"}
                async for event in run_build(pr_repo, pr_branch, service=svc, snapshot=snap):
                    yield event
                    if event.get("type") == "done" and not event.get("success"):
                        return
                yield {"type": "log", "data": f"  Build done. Shipper will deploy it."}
                needs_deploy.append({"repo": pr_repo, "branch": pr_branch, "service": svc, "snapshot": snap, "skip": False})
            else:
                # `deploycli --status` timed out. Trust the consolidator — let Shipper
                # try the deploy. If the snapshot doesn't exist, Shipper's deploy
                # will fail with "no snapshot found" and we surface that cleanly.
                yield {"type": "log", "data": f"  Status check timed out ({SNAPSHOT_STATUS_TIMEOUT}s). Letting Shipper attempt the deploy anyway."}
                needs_deploy.append({"repo": pr_repo, "branch": pr_branch, "service": svc, "snapshot": snap, "skip": False})

        pct = int(((i + 1) / len(dev_info)) * 100)
        yield {"type": "progress", "pct": pct, "eta": ""}

    # ── Step 3: Shipper — trigger deploys, then hand off to user for recheck ──
    yield {"type": "stage_change", "stage": "shipper"}

    all_deployed = all(d["skip"] for d in needs_deploy)
    pending_deploys = [d for d in needs_deploy if not d["skip"]]

    if all_deployed:
        yield {"type": "log", "data": "[Shipper] All snapshots already deployed, env clean!"}
        yield {"type": "progress", "pct": 100, "eta": ""}
    else:
        # Stale resets already happened in Step 1 cleanup. Shipper only deploys
        # this ticket's snapshots from here.

        # Dedup by service BEFORE issuing deploys — two PRs on the same service
        # must not produce two back-to-back deploy commands; Deploy would
        # restart the rollout on the second and the env stays stuck. Last
        # snapshot wins (matches the previous pipeline contract).
        deploy_targets: dict[str, str] = {}
        for d in pending_deploys:
            deploy_targets[d["service"]] = d["snapshot"]

        # Issue exactly one deploy command per service.
        succeeded = []
        failed = []
        for svc, snap in deploy_targets.items():
            yield {"type": "log", "data": f"[Shipper] Deploying {snap} to {env_name}/{svc}"}
            deploy_code, deploy_lines = await _run_cmd_and_capture(
                f"deploycli --skip-update deploy {env_name}/{svc} --snapshot {snap} -y",
                timeout=120,
            )
            # Scan for other-snapshot dependencies that Deploy will co-deploy.
            # When deploycli prints the services table, any row marked 'recreate' or 'update'
            # means Deploy will touch that service too. Flag them so the user knows
            # what's happening alongside their target snapshot.
            clean_lines = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in deploy_lines]
            co_deploys = []
            in_services_table = False
            for cl in clean_lines:
                if "| Services |" in cl or "+----------+" in cl:
                    in_services_table = True
                    continue
                if in_services_table and "| Resources |" in cl:
                    in_services_table = False
                    continue
                if in_services_table:
                    # Rows with 'recreate' or 'update' action are services Deploy will change
                    if re.search(r'\b(recreate|update|create)\b', cl, re.IGNORECASE):
                        # Extract service name from row (first non-empty token)
                        name_match = re.match(r'\s*(\S[\w\-]+)', cl)
                        if name_match:
                            co_name = name_match.group(1).rstrip('.')
                            if co_name != svc:
                                co_deploys.append(co_name)
            for line in deploy_lines:
                yield {"type": "log", "data": f"  {line}"}
            if co_deploys:
                yield {"type": "log", "data": f"  [Shipper] Note: Deploy will also update these services as dependencies: {', '.join(co_deploys)}"}
                yield {"type": "log", "data": f"  [Shipper] If any of those fail, 'Check Deploy' will show which one and link its Jenkins build log."}
            if deploy_code == 0:
                succeeded.append({"service": svc, "snapshot": snap})
            else:
                failed.append({"service": svc, "snapshot": snap, "exit": deploy_code})
                yield {"type": "log", "data": f"[Shipper] !! Deploy command failed for {svc} (exit {deploy_code}) — see lines above."}

        deploy_services = succeeded  # only ask Check Deploy to poll the ones that actually got past the CLI
        yield {"type": "log", "data": ""}
        if failed:
            yield {"type": "log", "data": f"[Shipper] {len(failed)} deploy(s) failed at the CLI step. Snapshot probably missing or env unreachable — fix and retry."}
        yield {"type": "log", "data": f"[Shipper] {len(succeeded)} deploy(s) triggered. Deploys take ~20 min."}
        yield {"type": "log", "data": "[Shipper] Click 'Check Deploy' when ready."}
        yield {"type": "shipper_ready", "env": env_name, "services": deploy_services}
        yield {"type": "progress", "pct": 10, "eta": "~20 min"}
        yield {"type": "done", "success": len(failed) == 0, "waiting_for_deploy": True}
        return

    # Resolve env URL if still empty. Use qa_target_host_for so plugin-only tickets
    # land on the host app's URL (service-cms) rather than the plugin's own host.
    if not env_url:
        env_url = await _resolve_test_env_url(env_name, list(ticket_snapshots.keys())) or env_url

    # Stage 3: Inspector — wait for user to run /qa-evidence, then click "Check Evidence"
    # run_test always ends with done waiting_for_evidence — pipeline ends here.
    # Frontend's handleCheckEvidence advances to Scribe when evidence is found.
    yield {"type": "stage_change", "stage": "inspector"}
    yield {"type": "log", "data": f"[Inspector] Env URL: {env_url or 'not detected'}"}
    async for event in run_test(ticket_key, env_url):
        yield event


async def watch_evidence(ticket_key):
    """Watch ~/evidence/{key} and yield events when evidence changes."""
    last_status = None
    for _ in range(360):
        ev = check_evidence(ticket_key)
        if ev["status"] != last_status:
            last_status = ev["status"]
            yield {"type": "evidence_update", "evidence": ev}
            if ev["status"] in ("tested", "published"):
                yield {"type": "done", "success": True, "evidence": ev}
                return
        await asyncio.sleep(10)


def _run_confidence(run_dir):
    """Headline confidence (or score) for a completed run, else None.

    None means the run has no summary.json — i.e. it has not completed.
    """
    import json as _json
    summary_path = os.path.join(run_dir, "summary.json")
    if not os.path.exists(summary_path):
        return None
    try:
        with open(summary_path, encoding="utf-8") as f:
            summary = _json.load(f)
    except Exception:
        return None
    conf = summary.get("confidence")
    if isinstance(conf, dict) and isinstance(conf.get("headline"), (int, float)):
        return conf["headline"]
    if isinstance(conf, (int, float)):
        return conf
    score = summary.get("score")
    if isinstance(score, dict) and isinstance(score.get("pct"), (int, float)):
        return score["pct"]
    if isinstance(score, (int, float)):
        return score
    return None


def _select_display_run(runs_path):
    """Pick the run the dashboard should display.

    Prefer the highest-confidence COMPLETED run (one with a summary.json),
    tie-breaking on the newest run id. Fall back to the newest run when none have
    completed yet (in-progress/partial). Returns a run name or None.

    Replaces a bare ``sorted(reverse=True)[0]`` that always showed the
    lexicographically-latest run — so a redundant/lower-quality re-run (e.g. a
    PASS-WITH-ISSUES retry numbered -002) would shadow the cleaner finished -001.
    """
    if not os.path.isdir(runs_path):
        return None
    run_names = sorted(
        [n for n in os.listdir(runs_path) if os.path.isdir(os.path.join(runs_path, n))],
        reverse=True,
    )
    if not run_names:
        return None
    completed = [n for n in run_names
                 if os.path.exists(os.path.join(runs_path, n, "summary.json"))]
    if not completed:
        return run_names[0]
    # run_names is newest-first; max() over (confidence, name) tie-breaks to the
    # newest run id when confidences are equal.
    def _rank(n):
        c = _run_confidence(os.path.join(runs_path, n))
        return (c if isinstance(c, (int, float)) else -1, n)
    return max(completed, key=_rank)


def _report_missing_screenshots(run_dir):
    """True when a run has screenshots on disk but its index.html embeds none.

    The qa-evidence skill sometimes writes a thin (link-only) index.html that the
    backend's image-embedding generator never overwrote. A regenerated report
    embeds base64 PNGs and is large, so a small report sitting next to PNGs is the
    thin one to regenerate. The size check avoids reading the whole file on polls.
    """
    index_path = os.path.join(run_dir, "index.html")
    if not os.path.exists(index_path):
        return False  # 'report missing' is handled by a separate branch
    try:
        if os.path.getsize(index_path) > 60_000:
            return False  # already image-rich
    except OSError:
        return False
    automated = os.path.join(run_dir, "automated")
    if not os.path.isdir(automated):
        return False
    for _root, _dirs, files in os.walk(automated):
        if any(f.lower().endswith((".png", ".jpg", ".jpeg")) for f in files):
            return True
    return False


def _report_status_stale(run_dir):
    """True when index.html still shows UNKNOWN TC badges while summary.json already
    carries real verdicts (pass/fail/blocked/...).

    The qa-evidence agent writes summary.json AND its own index.html, sometimes
    emitting the report before its per-TC verdicts are final — leaving an all-UNKNOWN
    report. Because the agent writes that report LAST, `summary_newer` is False, and
    because the report can still be large/image-rich, `_report_missing_screenshots` is
    False too — so the stale report would never be replaced. This catches that case so
    the authoritative summary.json render (which has the real statuses) wins.
    """
    index_path = os.path.join(run_dir, "index.html")
    summary_path = os.path.join(run_dir, "summary.json")
    if not (os.path.exists(index_path) and os.path.exists(summary_path)):
        return False
    import json as _json
    try:
        with open(summary_path, encoding="utf-8") as f:
            summary = _json.load(f)
    except Exception:
        return False
    tcs = summary.get("test_results") or summary.get("test_cases") or []
    real = {str((tc.get("status") or "")).lower() for tc in tcs}
    has_real_verdicts = bool(real & {
        "pass", "fail", "blocked", "incomplete", "skipped", "not-executed",
    })
    if not has_real_verdicts:
        return False  # summary has nothing better to render — leave the report alone
    try:
        with open(index_path, encoding="utf-8", errors="replace") as f:
            html = f.read()
    except Exception:
        return False
    # The TC status badge renders the upper-cased status as ">STATUS<". An UNKNOWN
    # badge present while summary has real verdicts means the report is stale.
    return ">UNKNOWN<" in html


def _report_url_for(ticket_key, run_name, run_dir=None):
    """Build the dashboard URL for the View Report button.

    Only returns a URL when `index.html` (the Phase 7 portal) exists. The earlier
    fallback to summary.json / headless.log made the button open raw JSON or a log
    file under a label that promises a rendered report, which confused reviewers.
    If the portal is missing, return "" so the button hides entirely — a signal to
    regenerate the run.

    Appends ``?v=<index mtime>`` so a regenerated report is fetched fresh rather
    than served from the browser/StaticFiles cache (the URL is otherwise stable
    across regenerations, which made screenshots appear only after a hard refresh).
    """
    if not run_name:
        return ""
    base = f"/evidence/{ticket_key}/runs/{run_name}"
    if run_dir is None:
        # Caller has no on-disk handle; trust the caller checked.
        return f"{base}/index.html"
    index_path = os.path.join(run_dir, "index.html")
    if os.path.exists(index_path):
        try:
            return f"{base}/index.html?v={int(os.path.getmtime(index_path))}"
        except OSError:
            return f"{base}/index.html"
    return ""


def check_evidence(ticket_key):
    evidence_path = os.path.join(EVIDENCE_DIR, ticket_key)
    if not os.path.isdir(evidence_path):
        return {"status": "none", "score": None, "time": "", "reportPath": "", "reportUrl": "", "needsReport": False}

    runs_path = os.path.join(evidence_path, "runs")
    if not os.path.isdir(runs_path):
        return {"status": "manifest", "score": None, "time": "", "reportPath": "", "reportUrl": "", "needsReport": False}

    runs = sorted(
        [name for name in os.listdir(runs_path)
         if os.path.isdir(os.path.join(runs_path, name))],
        reverse=True,
    )
    if not runs:
        return {"status": "manifest", "score": None, "time": "", "reportPath": "", "reportUrl": "", "needsReport": False}

    latest_run_name = _select_display_run(runs_path) or runs[0]
    latest_run = os.path.join(runs_path, latest_run_name)
    # Heal a thin/image-less report for the run we're about to display, so the
    # dashboard always serves the screenshot-embedded version (one-time per run —
    # a regenerated report is large, so _report_missing_screenshots goes False).
    if _report_missing_screenshots(latest_run):
        try:
            generate_html_report(ticket_key, latest_run_name)
        except Exception:
            pass
    report_path = os.path.join(latest_run, "index.html")
    # A 0-byte index.html counts as missing — some generators leave an empty
    # placeholder in the run dir while writing the real portal to the ticket root.
    has_html = os.path.exists(report_path) and os.path.getsize(report_path) > 0
    score = None
    time_taken = ""

    import json as _json
    summary_path = os.path.join(latest_run, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            summary = _json.load(f)
        score = summary.get("score")
        # Some runs write `score` as a tally dict (e.g. {pass, fail, blocked, total, pct, verdict}).
        # Coerce to a single number so the API contract (number | null) holds for the UI.
        if isinstance(score, dict):
            pct = score.get("pct")
            if isinstance(pct, (int, float)):
                score = round(pct)
            else:
                total = score.get("total") or 0
                passed = score.get("pass") or 0
                score = round(100 * passed / total) if total else None
        if score is None:
            _raw_conf = summary.get("confidence")
            if isinstance(_raw_conf, dict):
                score = _raw_conf.get("headline")
            elif isinstance(_raw_conf, (int, float)):
                score = _raw_conf
        time_taken = summary.get("time", "") or summary.get("date", "")

    # Resolve the report URL/path. Prefer the run-level portal; fall back to the
    # ticket-root index.html for runs whose generator wrote the portal there and
    # left an empty placeholder in the run dir. needsReport=True tells the UI to
    # show "Generate Report" instead of a (broken) "View Report".
    if has_html:
        report_url = _report_url_for(ticket_key, latest_run_name, latest_run)
        resolved_path = report_path
    else:
        root_html = os.path.join(evidence_path, "index.html")
        if os.path.exists(root_html) and os.path.getsize(root_html) > 0:
            report_url = f"/evidence/{ticket_key}/index.html"
            resolved_path = root_html
            has_html = True
        else:
            report_url = _report_url_for(ticket_key, latest_run_name, latest_run)
            resolved_path = ""

    # Sum Claude token cost across all runs that have session tracking in infra.json
    claude_cost = _otel.total_cost_for_ticket(runs_path)

    return {
        "status": "tested",
        "score": score,
        "time": time_taken,
        "reportPath": resolved_path,
        "reportUrl": report_url,
        "needsReport": not has_html and _run_has_content(latest_run),
        "latestRun": latest_run_name,
        "claudeCost": claude_cost,
    }


def generate_html_report(ticket_key, run_name=None):
    """Build index.html for the given run (default: latest).

    Embeds summary, TC results, full-size screenshots with lightbox, markups,
    diffs, what-works/blocks callouts, and run-over-run delta.
    Returns (success: bool, message: str, report_url: str).
    """
    import base64
    import json as _json
    import html as _html_mod

    runs_path = os.path.join(EVIDENCE_DIR, ticket_key, "runs")
    if not os.path.isdir(runs_path):
        return False, "No runs directory found", ""

    # Load manifest.yml early so we can prefer a run that has populated results
    import yaml as _yaml
    manifest = {}
    manifest_path = os.path.join(EVIDENCE_DIR, ticket_key, "manifest.yml")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = _yaml.safe_load(f) or {}
        except Exception:
            manifest = {}

    if run_name is None:
        runs = sorted(
            [n for n in os.listdir(runs_path) if os.path.isdir(os.path.join(runs_path, n))],
            reverse=True,
        )
        if not runs:
            return False, "No runs found", ""
        # Prefer the most recent run whose manifest entry has populated results.
        # Newer empty runs (e.g. created but not yet executed) otherwise win and
        # produce an all-UNKNOWN report.
        runs_with_results = set()
        for r in manifest.get("runs", []) or []:
            if isinstance(r, dict) and isinstance(r.get("results"), dict) and r["results"]:
                runs_with_results.add(r.get("id"))
        preferred = next((n for n in runs if n in runs_with_results), None)
        run_name = preferred or runs[0]

    run_dir = os.path.join(runs_path, run_name)
    if not os.path.isdir(run_dir):
        return False, f"Run {run_name} not found", ""

    # Load summary
    summary = {}
    summary_path = os.path.join(run_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            summary = _json.load(f)
    # Build lookup: tc_id -> manifest TC entry
    manifest_tc_map = {tc["id"]: tc for tc in manifest.get("test_cases", []) if tc.get("id")}
    # Score/confidence from manifest if not in summary
    manifest_conf = manifest.get("confidence", {}) if isinstance(manifest.get("confidence"), dict) else {}

    # Build lookup: tc_id -> {status, note, method} from manifest.runs[this_run].results
    # qa-evidence skill writes per-TC outcomes here even when summary.json is missing.
    manifest_run_results = {}
    for r in manifest.get("runs", []) or []:
        if isinstance(r, dict) and r.get("id") == run_name:
            res = r.get("results", {}) or {}
            if isinstance(res, dict):
                for tc_id, entry in res.items():
                    if isinstance(entry, dict):
                        manifest_run_results[tc_id] = entry
                    else:
                        manifest_run_results[tc_id] = {"status": str(entry)}
            break

    ticket_summary = summary.get("ticket_summary", "") or summary.get("summary", "")
    verdict_raw = summary.get("verdict", "") or summary.get("verdict_reason", "")
    # verdict may be a long sentence — extract the first word for the badge
    verdict_word = verdict_raw.split()[0] if verdict_raw else ""
    verdict_long = verdict_raw if len(verdict_raw) > 10 else ""

    # Score: summary → manifest fallback
    raw_conf = summary.get("confidence")
    raw_score = summary.get("score") or summary.get("score_breakdown", {}).get("headline")
    if isinstance(raw_conf, dict):
        score = raw_conf.get("headline") or raw_score
        band = raw_conf.get("band", "")
        explanation = raw_conf.get("explanation", "")
    else:
        score = raw_conf or raw_score or manifest_conf.get("headline")
        band = (summary.get("confidence_band") or summary.get("band") or
                summary.get("score_band") or summary.get("score_breakdown", {}).get("band", "") or
                manifest_conf.get("band", ""))
        explanation = (summary.get("confidence_explanation") or
                       summary.get("score_breakdown", {}).get("explanation", "") or
                       manifest_conf.get("explanation", ""))

    # Task 4: prefer canonical pct from Task 3 scoring block (summary["score"]["pct"]).
    # Also handles the case where raw_score was incorrectly set to the dict itself.
    _score_block = summary.get("score")
    if isinstance(_score_block, dict) and _score_block.get("pct") is not None:
        score = _score_block["pct"]

    env_val = summary.get("env", "") or summary.get("env_passed", "")
    if isinstance(env_val, dict):
        env_str = env_val.get("core_cms") or next(iter(env_val.values()), "")
    else:
        env_str = env_val
    executor = summary.get("executor", "")
    started = summary.get("started", "")
    finished = summary.get("finished", "") or summary.get("completed", "") or summary.get("ended", "")
    what_works = summary.get("what_works", [])
    what_blocks = summary.get("what_blocks_full_pass", []) or summary.get("what_blocks", [])
    run_notes = summary.get("notes", "")
    totals = summary.get("totals", {})
    next_actions = summary.get("next_actions", [])
    compared_to = summary.get("compared_to_run_004") or summary.get("delta") or {}
    warnings = summary.get("warnings", [])
    timing = summary.get("timing", {})
    env_layout = summary.get("env_layout", {})
    infrastructure = summary.get("infrastructure", {})

    # Normalize TC list — support test_results (old), test_cases (new skill format),
    # and flat tc_results dict as fallback. Unify into tc_detail_map keyed by TC id.
    def _norm_tc(tc):
        """Normalize a TC entry to a common shape."""
        tc_id = tc.get("tc") or tc.get("id") or ""
        acs = tc.get("ac", [])
        if isinstance(acs, str):
            acs = [acs]
        return {
            "tc": tc_id,
            "title": tc.get("title", ""),
            "ac": acs,
            "status": tc.get("status", "unknown"),
            "result": tc.get("result") or tc.get("notes") or tc.get("note") or "",
            "evidence": tc.get("evidence", []),
            "priority": tc.get("priority", ""),
            "steps_log": tc.get("steps_log", False),
        }

    raw_tc_list = summary.get("test_results") or summary.get("test_cases") or []
    tc_list = [_norm_tc(tc) for tc in raw_tc_list]
    tc_detail_map = {tc["tc"]: tc for tc in tc_list if tc["tc"]}
    # Also support legacy flat tc_results dict
    tc_results_legacy = summary.get("tc_results", {})

    # Merge manifest TC metadata into tc_detail_map — fills title/ac/steps for
    # runs whose summary.json only has a flat tc_results dict
    for tc_id, m_tc in manifest_tc_map.items():
        m_run = manifest_run_results.get(tc_id, {})
        if tc_id not in tc_detail_map:
            # TC in manifest but not in summary — build from manifest + legacy result
            acs = m_tc.get("ac", [])
            if isinstance(acs, str):
                acs = [acs]
            tc_detail_map[tc_id] = {
                "tc": tc_id,
                "title": m_tc.get("title", ""),
                "ac": acs,
                "status": (tc_results_legacy.get(tc_id)
                           or m_run.get("status")
                           or "unknown"),
                "result": m_run.get("note", "") or m_run.get("notes", ""),
                "evidence": [],
                "priority": m_tc.get("priority", ""),
                "steps": m_tc.get("steps", []),
            }
        else:
            # TC already in map — backfill missing fields from manifest
            entry = tc_detail_map[tc_id]
            if not entry.get("title"):
                entry["title"] = m_tc.get("title", "")
            if not entry.get("ac"):
                acs = m_tc.get("ac", [])
                entry["ac"] = [acs] if isinstance(acs, str) else acs
            if not entry.get("priority"):
                entry["priority"] = m_tc.get("priority", "")
            entry.setdefault("steps", m_tc.get("steps", []))
            # Promote manifest run status when current entry is unknown/empty
            if (not entry.get("status") or entry.get("status") == "unknown") and m_run.get("status"):
                entry["status"] = m_run["status"]
            if not entry.get("result") and (m_run.get("note") or m_run.get("notes")):
                entry["result"] = m_run.get("note") or m_run.get("notes")

    # If verdict wasn't in summary, derive it from the merged TC statuses.
    if not verdict_word:
        statuses = [str(tc.get("status", "")).lower() for tc in tc_detail_map.values()]
        non_empty = [s for s in statuses if s and s != "unknown"]
        if non_empty and all(s == "pass" for s in non_empty):
            verdict_word = "PASS"
        elif any(s == "fail" for s in non_empty):
            verdict_word = "FAIL"
        elif any(s == "blocked" for s in non_empty):
            verdict_word = "BLOCKED"
        elif any(s in ("incomplete", "skipped", "not-executed") for s in non_empty):
            verdict_word = "PARTIAL"
        else:
            verdict_word = "UNKNOWN"

    verdict_colors = {
        "PASS": "#22c55e", "FAIL": "#ef4444", "PARTIAL": "#f59e0b",
        "BLOCKED": "#f97316", "UNKNOWN": "#94a3b8",
    }
    verdict_color = verdict_colors.get(verdict_word.upper(), "#94a3b8")
    band_color = {
        "high": "#22c55e", "pass-with-issues": "#f59e0b",
        "needs-review": "#f97316", "not-ready": "#ef4444",
    }.get(band, "#94a3b8")
    status_colors = {
        "pass": "#22c55e", "fail": "#ef4444", "skipped": "#94a3b8",
        "incomplete": "#f59e0b", "blocked": "#f97316", "not-executed": "#64748b",
        "unknown": "#94a3b8",
    }

    # --- helpers ---
    def _esc(s):
        return _html_mod.escape(str(s))

    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

    def _is_image(path):
        return path.lower().endswith(IMAGE_EXTS)

    def _img_data(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode()

    def _img_tag(path, alt="", extra_style=""):
        if not _is_image(path):
            return ""
        b64 = _img_data(path)
        if b64 is None:
            return f'<span style="color:#64748b;font-size:11px">missing: {_esc(os.path.basename(path))}</span>'
        lower = path.lower()
        if lower.endswith(".gif"):
            mime = "image/gif"
        elif lower.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif lower.endswith(".webp"):
            mime = "image/webp"
        else:
            mime = "image/png"
        return (f'<img src="data:{mime};base64,{b64}" alt="{_esc(alt)}" '
                f'style="max-width:100%;border-radius:6px;cursor:zoom-in;{extra_style}" '
                f'onclick="openLightbox(this.src)">')

    # Collect artifact dirs
    automated_dir = os.path.join(run_dir, "automated")
    markup_dir = os.path.join(run_dir, "markup")
    diffs_dir = os.path.join(run_dir, "diffs")
    manual_dir = os.path.join(run_dir, "manual")

    # Collect all TC ids from both dirs and summary
    tc_ids_from_dir = set()
    if os.path.isdir(automated_dir):
        for name in os.listdir(automated_dir):
            if os.path.isdir(os.path.join(automated_dir, name)):
                tc_ids_from_dir.add(name)
    tc_ids_from_summary = {tc.get("tc", "") for tc in tc_list if tc.get("tc")}
    tc_ids = sorted(tc_ids_from_dir | tc_ids_from_summary)

    # Collect flat screenshot PNGs in automated/ (not in subdirs), minus those
    # already referenced in tc evidence paths (to avoid duplicates in the report)
    all_evidence_basenames = set()
    for tc in tc_list:
        for ep in tc.get("evidence", []):
            all_evidence_basenames.add(os.path.basename(ep))
    flat_automated_imgs = []
    if os.path.isdir(automated_dir):
        flat_automated_imgs = sorted([
            f for f in os.listdir(automated_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
            and os.path.isfile(os.path.join(automated_dir, f))
            and f not in all_evidence_basenames
        ])

    # --- score ring SVG ---
    def _score_ring(s, color):
        if s is None:
            return ""
        pct = max(0, min(100, int(s)))
        circ = 2 * 3.14159 * 36
        dash = circ * pct / 100
        gap = circ - dash
        return f"""<svg width="90" height="90" viewBox="0 0 90 90">
  <circle cx="45" cy="45" r="36" fill="none" stroke="#1e293b" stroke-width="8"/>
  <circle cx="45" cy="45" r="36" fill="none" stroke="{color}" stroke-width="8"
    stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-linecap="round"
    transform="rotate(-90 45 45)"/>
  <text x="45" y="49" text-anchor="middle" fill="{color}" font-size="18" font-weight="700"
    font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">{pct}</text>
</svg>"""

    # --- AC section (above TC table) ---
    # Build from manifest acceptance_criteria[]
    ac_list = manifest.get("acceptance_criteria", [])
    ac_section_html = ""
    if ac_list:
        ac_items = ""
        for ac in ac_list:
            ac_id = _esc(ac.get("id", ""))
            ac_text = _esc(ac.get("text", ""))
            ac_source = _esc(ac.get("source", ""))
            ac_src_span = (f'<span style="color:#475569;font-size:10px;margin-left:8px">({ac_source})</span>'
                           if ac_source else "")
            ac_items += (
                f'<div id="ac-{ac_id}" style="padding:10px 14px;border-bottom:1px solid #1e293b;display:flex;gap:12px;align-items:flex-start">'
                f'<span style="background:#1e3a5f;color:#93c5fd;font-size:10px;font-weight:800;padding:2px 8px;border-radius:4px;white-space:nowrap;margin-top:2px">{ac_id}</span>'
                f'<div style="flex:1"><span style="color:#e2e8f0;font-size:13px;line-height:1.55">{ac_text}</span>'
                f'{ac_src_span}'
                f'</div></div>\n'
            )
        ac_section_html = f'''<div style="background:#1e293b;border-radius:10px;overflow:hidden;border:1px solid #1e3a5f;margin-bottom:24px">
  <div style="padding:12px 16px;border-bottom:1px solid #334155;font-weight:700;font-size:13px;color:#93c5fd">Acceptance Criteria</div>
  {ac_items}
</div>'''

    # --- TC summary table ---
    tc_row_by_id = {}
    for tc_id in tc_ids:
        tc_data = tc_detail_map.get(tc_id, {})
        result = tc_data.get("status") or tc_results_legacy.get(tc_id, "unknown")
        color = status_colors.get(result, "#94a3b8")
        title = tc_data.get("title", "")
        ac_tags = tc_data.get("ac", [])
        # Build linked AC badges
        ac_badges = " ".join(
            f'<a href="#ac-{_esc(a)}" style="background:#1e3a5f;color:#93c5fd;font-size:10px;font-weight:700;'
            f'padding:1px 6px;border-radius:4px;text-decoration:none;white-space:nowrap">{_esc(a)}</a>'
            for a in ac_tags
        )
        acs_plain = ", ".join(ac_tags)
        tc_row_by_id[tc_id] = (
            f'<tr onclick="document.getElementById(\'{tc_id}\').scrollIntoView({{behavior:\'smooth\'}})" '
            f'style="cursor:pointer">'
            f'<td><span style="color:#818cf8;font-weight:700">{_esc(tc_id)}</span></td>'
            f'<td style="color:#cbd5e1;font-size:12px">{_esc(title)}</td>'
            f'<td style="font-size:11px">{ac_badges if ac_badges else _esc(acs_plain)}</td>'
            f'<td><span style="color:{color};font-weight:700;font-size:12px">{result.upper()}</span></td>'
            f'</tr>\n'
        )

    # --- TC detail cards ---
    tc_detail_by_id = {}
    for tc_id in tc_ids:
        tc_data = tc_detail_map.get(tc_id, {})
        result = tc_data.get("status") or tc_results_legacy.get(tc_id, "unknown")
        color = status_colors.get(result, "#94a3b8")
        title = tc_data.get("title", "")
        ac_tags = tc_data.get("ac", [])
        narrative = tc_data.get("result", "")

        # Load notes.md / skipped.txt from subdir if present
        tc_path = os.path.join(automated_dir, tc_id)
        notes = ""
        skip_reason = ""
        if os.path.isdir(tc_path):
            notes_path = os.path.join(tc_path, "notes.md")
            if os.path.exists(notes_path):
                with open(notes_path) as f:
                    notes = f.read().strip()
            skipped_path = os.path.join(tc_path, "skipped.txt")
            if os.path.exists(skipped_path):
                with open(skipped_path) as f:
                    skip_reason = f.read().strip()

        # ---- Load steps-log.json ----
        steps_log = []
        steps_log_path = os.path.join(tc_path, "steps-log.json") if os.path.isdir(tc_path) else ""
        if steps_log_path and os.path.exists(steps_log_path):
            try:
                with open(steps_log_path) as f:
                    _sl_raw = _json.load(f) or []
                # Support both array format and object format with a "steps" key
                if isinstance(_sl_raw, dict):
                    steps_log = _sl_raw.get("steps", [])
                else:
                    steps_log = _sl_raw
            except Exception:
                steps_log = []

        # ---- Load field-assertions.json ----
        field_assertions = []
        fa_path = os.path.join(tc_path, "field-assertions.json") if os.path.isdir(tc_path) else ""
        if fa_path and os.path.exists(fa_path):
            try:
                with open(fa_path) as f:
                    field_assertions = _json.load(f) or []
            except Exception:
                field_assertions = []

        # ---- Load console.log (first 5 lines + counts from summary line) ----
        console_log_html = ""
        console_log_path = os.path.join(tc_path, "console.log") if os.path.isdir(tc_path) else ""
        if console_log_path and os.path.exists(console_log_path):
            try:
                with open(console_log_path) as f:
                    console_lines = f.readlines()
                # Parse summary line if present (format: "Total messages: N (Errors: N, Warnings: N)")
                error_count = 0
                first_line = console_lines[0].strip() if console_lines else ""
                if "Errors:" in first_line:
                    try:
                        import re as _re
                        m = _re.search(r'Errors:\s*(\d+)', first_line)
                        error_count = int(m.group(1)) if m else 0
                    except Exception:
                        error_count = 0
                err_badge_color = "#ef4444" if error_count > 0 else "#22c55e"
                err_badge = (f'<span style="background:{err_badge_color}20;color:{err_badge_color};'
                             f'font-size:10px;font-weight:700;padding:1px 7px;border-radius:4px;margin-left:8px">'
                             f'{error_count} errors</span>')
                preview_lines = [l.rstrip() for l in console_lines[:5]]
                preview_text = _esc("\n".join(preview_lines))
                console_log_html = (
                    f'<details style="margin-bottom:12px">'
                    f'<summary style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;'
                    f'letter-spacing:0.05em;cursor:pointer;user-select:none">Console Log{err_badge}</summary>'
                    f'<pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:10px;'
                    f'white-space:pre-wrap;max-height:160px;overflow-y:auto;color:#94a3b8;margin-top:8px">'
                    f'{preview_text}</pre>'
                    f'</details>'
                )
            except Exception:
                console_log_html = ""

        # Screenshots: from summary evidence paths first, then tc_path dir.
        # Only embed image files — JSON/log/txt evidence is referenced elsewhere
        # and should not be shoved into <img> tags.
        evidence_paths = tc_data.get("evidence", [])
        screenshots_from_evidence = []
        for ep in evidence_paths:
            if not _is_image(ep):
                continue
            full = os.path.join(run_dir, ep)
            if os.path.exists(full):
                screenshots_from_evidence.append((os.path.basename(ep), full))

        # Also catch any image files in automated/TC-ID/ not listed in evidence
        dir_shots = []
        if os.path.isdir(tc_path):
            for s in sorted(os.listdir(tc_path)):
                if _is_image(s):
                    full = os.path.join(tc_path, s)
                    already = any(p == full for _, p in screenshots_from_evidence)
                    if not already:
                        dir_shots.append((s, full))

        all_shots = screenshots_from_evidence + dir_shots
        # Build a quick lookup: filename -> full path for steps-log screenshot linking
        shot_by_name = {label: path for label, path in all_shots}

        # Markup images for this TC
        markup_imgs = []
        if os.path.isdir(markup_dir):
            for m in sorted(os.listdir(markup_dir)):
                if m.startswith(tc_id) and _is_image(m):
                    markup_imgs.append((m, os.path.join(markup_dir, m)))

        # ---- Steps-log table (primary evidence view) ----
        steps_log_table_html = ""
        if steps_log:
            step_rows = ""
            for entry in steps_log:
                step_num = entry.get("step", "")
                action = _esc(str(entry.get("action", "")))
                expected = _esc(str(entry.get("expected", "")))
                actual = _esc(str(entry.get("actual", "")))
                step_result = str(entry.get("result", "")).lower()
                shot_file = entry.get("screenshot", "")
                res_color = "#22c55e" if step_result == "pass" else ("#ef4444" if step_result == "fail" else "#94a3b8")
                res_symbol = "&#10003;" if step_result == "pass" else ("&#10007;" if step_result == "fail" else "&#8212;")
                # Screenshot thumbnail inline below the row
                shot_html = ""
                if shot_file and shot_file in shot_by_name:
                    shot_html = (
                        f'<tr><td colspan="5" style="padding:4px 12px 10px;background:#0a1525">'
                        f'<div style="font-size:9px;color:#475569;margin-bottom:4px;font-family:monospace">{_esc(shot_file)}</div>'
                        f'{_img_tag(shot_by_name[shot_file], shot_file, "max-width:480px;border-radius:4px;")}'
                        f'</td></tr>'
                    )
                step_rows += (
                    f'<tr>'
                    f'<td style="color:#64748b;font-size:11px;text-align:center;white-space:nowrap">{_esc(str(step_num))}</td>'
                    f'<td style="color:#cbd5e1;font-size:12px">{action}</td>'
                    f'<td style="color:#94a3b8;font-size:12px">{expected}</td>'
                    f'<td style="color:#e2e8f0;font-size:12px">{actual}</td>'
                    f'<td style="text-align:center;background:{res_color}15">'
                    f'<span style="color:{res_color};font-weight:700;font-size:14px">{res_symbol}</span></td>'
                    f'</tr>'
                )
                if shot_html:
                    step_rows += shot_html
            steps_log_table_html = f'''<div style="margin-bottom:16px">
  <div style="font-size:11px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px">Step-by-Step Evidence</div>
  <div style="overflow-x:auto">
  <table style="font-size:12px;min-width:600px">
  <thead><tr>
    <th style="width:40px">#</th>
    <th>Action</th>
    <th style="width:220px">Expected</th>
    <th style="width:220px">Actual</th>
    <th style="width:50px">Result</th>
  </tr></thead>
  <tbody>{step_rows}</tbody>
  </table>
  </div>
</div>'''

        # ---- Field-assertions table ----
        field_assert_html = ""
        if field_assertions:
            fa_rows = ""
            any_fail = False
            for fa in field_assertions:
                field = _esc(str(fa.get("field", "")))
                exp = _esc(str(fa.get("expected", "")))
                act = _esc(str(fa.get("actual", "")))
                match = fa.get("match", True)
                if not match:
                    any_fail = True
                row_bg = "rgba(239,68,68,0.07)" if not match else ""
                match_sym = '&#10003;' if match else '&#10007;'
                match_color = "#22c55e" if match else "#ef4444"
                fa_rows += (
                    f'<tr style="background:{row_bg}">'
                    f'<td style="font-family:monospace;font-size:11px;color:#93c5fd">{field}</td>'
                    f'<td style="font-size:11px;color:#94a3b8">{exp}</td>'
                    f'<td style="font-size:11px;color:#e2e8f0">{act}</td>'
                    f'<td style="text-align:center"><span style="color:{match_color};font-weight:700">{match_sym}</span></td>'
                    f'</tr>'
                )
            header_color = "#ef4444" if any_fail else "#22c55e"
            field_assert_html = f'''<div style="margin-bottom:16px">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;color:{header_color}">Field Persistence Assertions</div>
  <table style="font-size:12px">
  <thead><tr><th>Field</th><th>Expected</th><th>Actual</th><th style="width:60px">Match</th></tr></thead>
  <tbody>{fa_rows}</tbody>
  </table>
</div>'''

        # ---- API / JSON evidence (api-*.json, console-errors.json, network-requests.json, …) ----
        # These artifacts are captured on disk and listed in tc evidence, but were never
        # surfaced in the report — so the API-flow proof was invisible. steps-log.json and
        # field-assertions.json are already rendered as rich tables above, so skip them.
        _json_already_rendered = {"steps-log.json", "field-assertions.json"}
        _json_candidates = {}  # basename -> full path
        for _ep in evidence_paths:
            if str(_ep).lower().endswith(".json"):
                _bn = os.path.basename(_ep)
                _full = os.path.join(run_dir, _ep)
                if _bn not in _json_already_rendered and os.path.exists(_full):
                    _json_candidates[_bn] = _full
        if os.path.isdir(tc_path):
            for _fn in sorted(os.listdir(tc_path)):
                if _fn.lower().endswith(".json") and _fn not in _json_already_rendered:
                    _full = os.path.join(tc_path, _fn)
                    if os.path.isfile(_full):
                        _json_candidates.setdefault(_fn, _full)
        json_evidence_html = ""
        if _json_candidates:
            _blocks = ""
            for _fn in sorted(_json_candidates):
                try:
                    with open(_json_candidates[_fn], encoding="utf-8", errors="replace") as _jf:
                        _raw = _jf.read()
                    try:
                        _pretty = _json.dumps(_json.loads(_raw), indent=2)
                    except Exception:
                        _pretty = _raw
                    _trunc = len(_pretty) > 6000
                    _body = _esc(_pretty[:6000]) + ("\n… (truncated)" if _trunc else "")
                except Exception:
                    _body = "(could not read artifact)"
                _blocks += (
                    f'<details style="margin-bottom:8px">'
                    f'<summary style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;'
                    f'letter-spacing:0.05em;cursor:pointer;user-select:none">{_esc(_fn)}</summary>'
                    f'<pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:10px;'
                    f'white-space:pre-wrap;max-height:340px;overflow-y:auto;color:#94a3b8;margin-top:8px">{_body}</pre>'
                    f'</details>'
                )
            json_evidence_html = (
                '<div style="margin-bottom:14px">'
                '<div style="font-size:11px;color:#64748b;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.05em;margin-bottom:8px">API / JSON Evidence</div>'
                f'{_blocks}</div>'
            )

        # ---- Assertion hints checklist (fallback when no steps-log) ----
        hints_html = ""
        m_tc = manifest_tc_map.get(tc_id, {})
        assertion_hints = m_tc.get("assertion_hints", [])
        if not steps_log and assertion_hints:
            hint_items = "".join(
                f'<li style="padding:3px 0;color:#64748b;font-size:12px;line-height:1.5">'
                f'<span style="color:#475569;margin-right:6px">&#9744;</span>{_esc(h)}</li>'
                for h in assertion_hints
            )
            hints_html = (
                '<div style="background:#0f172a;border:1px solid #334155;border-radius:6px;'
                'padding:10px 14px;margin-bottom:14px">'
                '<div style="font-size:10px;color:#475569;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.05em;margin-bottom:8px">What should have been verified</div>'
                f'<ul style="margin:0;padding-left:0;list-style:none">{hint_items}</ul>'
                '</div>'
            )

        # ---- Screenshot gallery (secondary, or primary when no steps-log) ----
        shots_html = ""
        if all_shots:
            shots_inner = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-top:10px">'
            for label, path in all_shots:
                display = label.replace(f"{tc_id}_", "").replace(".png", "").replace("_", " ")
                shots_inner += f'''<div style="background:#0f172a;border-radius:8px;padding:8px;border:1px solid #334155">
  <div style="font-size:10px;color:#64748b;margin-bottom:6px;font-family:monospace">{_esc(label)}</div>
  <div style="font-size:12px;color:#94a3b8;margin-bottom:8px">{_esc(display)}</div>
  {_img_tag(path, label, "width:100%;border-radius:4px;")}
</div>'''
            shots_inner += '</div>'
            # Open by default only when there's no steps-log
            gallery_open = "open" if not steps_log else ""
            shots_html = (
                f'<details {gallery_open} style="margin-top:12px">'
                f'<summary style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:0.05em;cursor:pointer;user-select:none">Screenshots ({len(all_shots)})</summary>'
                f'{shots_inner}'
                f'</details>'
            )

        # Markup grid — highlighted with purple border
        markups_html = ""
        if markup_imgs:
            markups_html = '<div style="margin-top:16px"><div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span style="background:#4f46e5;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px">ANNOTATED</span><span style="color:#818cf8;font-size:12px">Visual Markup</span></div>'
            markups_html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">'
            for label, path in markup_imgs:
                display = label.replace(f"{tc_id}_", "").replace(".png", "").replace("_annotated", " ✏").replace("_", " ")
                markups_html += f'''<div style="background:#1e1b4b;border-radius:8px;padding:8px;border:2px solid #4f46e5">
  <div style="font-size:11px;color:#a5b4fc;margin-bottom:8px">{_esc(display)}</div>
  {_img_tag(path, label, "width:100%;")}
</div>'''
            markups_html += '</div></div>'

        # Status badge bg
        status_bg = {
            "pass": "rgba(34,197,94,0.1)", "fail": "rgba(239,68,68,0.1)",
            "blocked": "rgba(249,115,22,0.1)", "not-executed": "rgba(100,116,139,0.1)",
        }.get(result, "rgba(148,163,184,0.05)")

        manifest_steps = tc_data.get("steps", [])
        priority = tc_data.get("priority", "")

        manifest_steps_html = ""
        if manifest_steps and not steps_log:
            # Only show manifest steps list when there is no steps-log (avoid duplication)
            step_items = "".join(
                f'<li style="padding:3px 0;color:#94a3b8;font-size:12px;line-height:1.5">{_esc(s)}</li>'
                for s in manifest_steps
            )
            manifest_steps_html = (
                '<details style="margin-bottom:12px">'
                '<summary style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.05em;cursor:pointer;user-select:none">Test Steps (from manifest)</summary>'
                f'<ol style="margin:8px 0 0;padding-left:20px">{step_items}</ol>'
                '</details>'
            )

        narrative_html = ""
        if narrative:
            narrative_html = (
                '<div style="background:#0f172a;border-left:3px solid #3b82f6;padding:12px 16px;'
                'border-radius:0 6px 6px 0;margin-bottom:14px">'
                '<div style="font-size:11px;color:#64748b;font-weight:700;text-transform:uppercase;'
                'letter-spacing:0.05em;margin-bottom:6px">Test Result Narrative</div>'
                f'<p style="color:#e2e8f0;font-size:13px;line-height:1.6;margin:0">{_esc(narrative)}</p>'
                '</div>'
            )

        priority_html = (f'<span style="color:#64748b;font-size:10px;font-weight:700;background:#0f172a;'
                         f'padding:1px 7px;border-radius:4px">{_esc(priority)}</span>') if priority else ""
        # AC badges in header, linked to AC section
        acs_header_html = " ".join(
            f'<a href="#ac-{_esc(a)}" style="background:#1e3a5f;color:#93c5fd;font-size:10px;font-weight:700;'
            f'padding:1px 6px;border-radius:4px;text-decoration:none">{_esc(a)}</a>'
            for a in ac_tags
        )
        title_html = f'<div style="color:#cbd5e1;font-size:13px;margin-top:4px">{_esc(title)}</div>' if title else ""
        notes_html = (f'<pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:11px;'
                      f'white-space:pre-wrap;max-height:200px;overflow-y:auto;color:#94a3b8">{_esc(notes)}</pre>') if notes else ""
        skip_html = (f'<div style="color:#f59e0b;font-size:12px;padding:8px 12px;background:rgba(245,158,11,0.1);'
                     f'border-radius:6px;margin-bottom:8px">Skipped: {_esc(skip_reason)}</div>') if skip_reason else ""

        tc_detail_by_id[tc_id] = f'''
<div id="{tc_id}" style="background:#1e293b;border-radius:10px;padding:20px;margin-bottom:20px;border:1px solid #334155;scroll-margin-top:80px">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-weight:800;font-size:15px;color:#f1f5f9">{_esc(tc_id)}</span>
        <span style="background:{status_bg};color:{color};font-weight:700;font-size:11px;padding:3px 10px;border-radius:12px;border:1px solid {color}40">{result.upper()}</span>
        {acs_header_html}
        {priority_html}
      </div>
      {title_html}
    </div>
  </div>
  {manifest_steps_html}
  {narrative_html}
  {hints_html}
  {steps_log_table_html}
  {field_assert_html}
  {console_log_html}
  {json_evidence_html}
  {notes_html}
  {skip_html}
  {shots_html}
  {markups_html}
</div>
'''

    # --- Split TCs into scored vs advisory; reassemble two-group HTML ---
    import qa_scoring as _qa_scoring
    _tcs_for_split = [{"id": tc_id} for tc_id in tc_ids]
    _scored_tcs, _advisory_tcs = _qa_scoring.split_test_cases(_tcs_for_split)
    _scored_ids = [t["id"] for t in _scored_tcs]
    _advisory_ids = [t["id"] for t in _advisory_tcs]

    # Summary table rows: scored first, then advisory with separator
    tc_rows_html = "".join(tc_row_by_id.get(tc_id, "") for tc_id in _scored_ids)
    if _advisory_ids:
        tc_rows_html += (
            '<tr><td colspan="4" style="background:#0f172a;color:#64748b;font-size:10px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:0.05em;padding:8px 12px">'
            'Advisory — not scored</td></tr>'
        )
        tc_rows_html += "".join(tc_row_by_id.get(tc_id, "") for tc_id in _advisory_ids)

    # Detail cards: scored section first, then advisory section under separate heading
    tc_detail_html = "".join(tc_detail_by_id.get(tc_id, "") for tc_id in _scored_ids)
    if _advisory_ids:
        _advisory_detail_html = "".join(tc_detail_by_id.get(tc_id, "") for tc_id in _advisory_ids)
        tc_detail_html += (
            '<h3 style="color:#94a3b8;margin:32px 0 16px;font-size:15px">'
            'Advisory <span style="font-size:11px;color:#475569;font-weight:400">— not scored</span></h3>'
            + _advisory_detail_html
        )

    # --- Flat automated screenshots (not in subdirs) ---
    flat_shots_html = ""
    if flat_automated_imgs:
        flat_shots_html = '<h3 style="color:#f1f5f9;margin:24px 0 12px">Additional Screenshots</h3>'
        flat_shots_html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">'
        for s in flat_automated_imgs:
            display = s.replace(".png", "").replace("_", " ")
            flat_shots_html += f'''<div style="background:#1e293b;border-radius:8px;padding:10px;border:1px solid #334155">
  <div style="font-size:11px;color:#64748b;margin-bottom:6px">{_esc(display)}</div>
  {_img_tag(os.path.join(automated_dir, s), s, "width:100%;")}
</div>'''
        flat_shots_html += '</div>'

    # --- Diffs section ---
    diffs_html = ""
    if os.path.isdir(diffs_dir):
        diff_files = sorted([f for f in os.listdir(diffs_dir) if _is_image(f)])
        if diff_files:
            diffs_html = '''<div style="margin-top:28px">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
  <span style="background:#0e7490;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:4px">VISUAL DIFFS</span>
  <span style="color:#94a3b8;font-size:13px">Before / After Comparison</span>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px">'''
            for d in diff_files:
                label = d.replace(".png", "").replace("_", " ")
                diffs_html += f'''<div style="background:#1e293b;border-radius:8px;padding:10px;border:1px solid #0e7490">
  <div style="font-size:11px;color:#67e8f9;margin-bottom:8px">{_esc(label)}</div>
  {_img_tag(os.path.join(diffs_dir, d), d, "width:100%;")}
</div>'''
            diffs_html += '</div></div>'

    # --- Manual evidence ---
    manual_html = ""
    if os.path.isdir(manual_dir):
        manual_imgs = sorted([f for f in os.listdir(manual_dir) if _is_image(f)])
        if manual_imgs:
            manual_html = '''<div style="margin-top:28px">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
  <span style="background:#7c3aed;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:4px">MANUAL EVIDENCE</span>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">'''
            for m in manual_imgs:
                manual_html += f'''<div style="background:#1e293b;border-radius:8px;padding:10px;border:1px solid #7c3aed">
  <div style="font-size:11px;color:#c4b5fd;margin-bottom:8px">{_esc(m.replace(".png","").replace("_"," "))}</div>
  {_img_tag(os.path.join(manual_dir, m), m, "width:100%;")}
</div>'''
            manual_html += '</div></div>'

    # --- What Works / What Blocks callouts ---
    callouts_html = ""
    if what_works or what_blocks:
        callouts_html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:24px 0">'
        if what_works:
            items = "".join(f'<li style="padding:4px 0">{_esc(w)}</li>' for w in what_works)
            callouts_html += f'''<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:16px">
  <div style="color:#22c55e;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px">✓ What Works</div>
  <ul style="margin:0;padding-left:18px;color:#86efac;font-size:12px;line-height:1.6">{items}</ul>
</div>'''
        if what_blocks:
            items = "".join(f'<li style="padding:4px 0">{_esc(w)}</li>' for w in what_blocks)
            callouts_html += f'''<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:16px">
  <div style="color:#ef4444;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px">⚠ Blockers</div>
  <ul style="margin:0;padding-left:18px;color:#fca5a5;font-size:12px;line-height:1.6">{items}</ul>
</div>'''
        callouts_html += '</div>'

    # --- Next actions ---
    next_html = ""
    if next_actions:
        items = "".join(f'<li style="padding:5px 0;color:#e2e8f0;font-size:12px">{_esc(a)}</li>' for a in next_actions)
        next_html = f'''<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;margin:20px 0">
  <div style="color:#f59e0b;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px">Next Actions</div>
  <ol style="margin:0;padding-left:20px;line-height:1.7">{items}</ol>
</div>'''

    # --- Run delta ---
    delta_html = ""
    if compared_to:
        prev_verdict = compared_to.get("run_004_verdict") or compared_to.get("prev_verdict", "")
        delta_text = compared_to.get("run_005_delta") or compared_to.get("delta", "")
        if prev_verdict or delta_text:
            delta_html = f'''<div style="background:#1e293b;border:1px solid #0369a1;border-radius:10px;padding:16px;margin:20px 0">
  <div style="color:#38bdf8;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px">Run-over-Run Progress</div>
  {f'<div style="color:#94a3b8;font-size:12px;margin-bottom:6px">Previous: <span style="color:#fca5a5">{_esc(prev_verdict)}</span></div>' if prev_verdict else ""}
  {f'<div style="color:#bae6fd;font-size:13px;line-height:1.6">{_esc(delta_text)}</div>' if delta_text else ""}
</div>'''

    # --- Env layout table ---
    env_layout_html = ""
    if env_layout:
        rows = ""
        for svc, info in env_layout.items():
            t = info.get("type", "")
            ver = info.get("version", info.get("snapshot", ""))
            note = info.get("note", "")
            type_color = {"snapshot": "#22c55e", "release": "#38bdf8", "reference": "#94a3b8"}.get(t, "#94a3b8")
            rows += (f'<tr><td style="font-weight:600;color:#f1f5f9">{_esc(svc)}</td>'
                     f'<td><span style="color:{type_color};font-size:11px;font-weight:700">{_esc(t.upper())}</span></td>'
                     f'<td style="font-family:monospace;font-size:11px;color:#94a3b8">{_esc(ver)}</td>'
                     f'<td style="font-size:11px;color:#64748b">{_esc(note)}</td></tr>')
        env_layout_html = f'''<details style="margin-top:20px">
  <summary style="color:#94a3b8;cursor:pointer;font-size:12px;font-weight:600">Environment Layout</summary>
  <table style="margin-top:10px;font-size:12px;width:100%">
  <thead><tr><th>Service</th><th>Type</th><th>Version</th><th>Note</th></tr></thead>
  <tbody>{rows}</tbody>
  </table>
</details>'''

    # --- Warnings ---
    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        warnings_html = f'<div style="background:rgba(245,158,11,0.08);border:1px solid #854d0e;border-radius:8px;padding:14px;margin-bottom:16px"><strong style="color:#fbbf24">⚠ Warnings</strong><ul style="margin:8px 0 0;padding-left:20px;color:#fcd34d;font-size:12px;line-height:1.7">{items}</ul></div>'

    # --- Timing ---
    timing_html = ""
    if timing:
        rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:#94a3b8'>{_esc(k.replace('_',' ').title())}</td>"
            f"<td style='font-weight:600;color:#f1f5f9'>{_esc(str(v))} min</td></tr>"
            for k, v in timing.items()
        )
        timing_html = f'<details style="margin-top:16px"><summary style="color:#94a3b8;cursor:pointer;font-size:12px">Timing</summary><table style="margin-top:8px;font-size:12px">{rows}</table></details>'

    # --- Run notes block ---
    run_notes_html = ""
    if run_notes:
        run_notes_html = (
            '<div style="background:#0f172a;border-left:3px solid #818cf8;padding:14px 18px;'
            'border-radius:0 8px 8px 0;margin:20px 0">'
            '<div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;'
            'letter-spacing:0.05em;margin-bottom:8px">Run Notes</div>'
            f'<p style="color:#e2e8f0;font-size:13px;line-height:1.6;margin:0">{_esc(run_notes)}</p>'
            '</div>'
        )

    # --- Totals bar ---
    totals_html = ""
    if totals:
        chips = []
        color_map = {"pass": "#22c55e", "fail": "#ef4444", "incomplete": "#f59e0b",
                     "blocked": "#f97316", "skipped": "#94a3b8", "not-executed": "#64748b"}
        for k, v in totals.items():
            if k == "tcs" or v == 0:
                continue
            c = color_map.get(k, "#94a3b8")
            chips.append(f'<span style="background:{c}20;color:{c};font-size:12px;font-weight:700;padding:3px 12px;border-radius:12px;border:1px solid {c}40">{v} {k.upper()}</span>')
        if chips:
            total_tcs = totals.get("tcs", "")
            label = f'<span style="font-size:12px;color:#64748b;margin-right:8px">{total_tcs} TCs:</span>' if total_tcs else ""
            totals_html = f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:16px 0">{label}{"".join(chips)}</div>'

    # --- Hero score section ---
    ring_svg = _score_ring(score, band_color if band else verdict_color)
    score_display = f'{score}/100' if score is not None else ""
    band_label = band.replace("-", " ").title() if band else ""

    date_str = ""
    if started:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            date_str = dt.strftime("%b %d, %Y %H:%M UTC")
        except Exception:
            date_str = started

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evidence: {_esc(ticket_key)} — {_esc(run_name)}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #f1f5f9; margin: 0; padding: 0; }}
  a {{ color: #818cf8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; text-align: left; vertical-align: top; }}
  th {{ color: #64748b; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; background: #0f172a; }}
  tr:hover td {{ background: rgba(255,255,255,0.02); }}
  details summary {{ user-select: none; }}
  .sticky-nav {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(15,23,42,0.95); backdrop-filter: blur(8px);
    border-bottom: 1px solid #1e293b; padding: 10px 24px;
    display: flex; align-items: center; gap: 16px;
  }}
  /* Lightbox */
  #lb {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.92); z-index:9999;
         align-items:center; justify-content:center; cursor:zoom-out; }}
  #lb.open {{ display:flex; }}
  #lb img {{ max-width:95vw; max-height:95vh; border-radius:8px; box-shadow:0 0 40px rgba(0,0,0,0.8); }}
</style>
</head>
<body>

<!-- Lightbox -->
<div id="lb" onclick="closeLightbox()">
  <img id="lb-img" src="" alt="">
</div>

<!-- Sticky nav -->
<div class="sticky-nav">
  <a href="https://acme.atlassian.net/browse/{_esc(ticket_key)}" target="_blank"
     style="font-weight:800;font-size:15px;color:#818cf8">{_esc(ticket_key)}</a>
  {f'<span style="color:#cbd5e1;font-size:13px">{_esc(ticket_summary)}</span>' if ticket_summary else ""}
  <span style="flex:1"></span>
  <span style="color:#64748b;font-size:11px">{_esc(run_name)}</span>
</div>

<!-- Hero -->
<div style="padding:32px 28px 24px;border-bottom:1px solid #1e293b;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%)">
  <div style="display:flex;align-items:center;gap:28px;flex-wrap:wrap">
    {ring_svg}
    <div>
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <span style="font-size:32px;font-weight:900;color:{verdict_color};letter-spacing:-0.02em">{_esc(verdict_word)}</span>
        {f'<span style="font-size:16px;font-weight:600;color:{band_color}">{_esc(score_display)}</span>' if score_display else ""}
        {f'<span style="background:{band_color}20;color:{band_color};font-size:11px;font-weight:700;padding:2px 10px;border-radius:12px">{_esc(band_label)}</span>' if band_label else ""}
      </div>
      {f'<p style="color:#94a3b8;font-size:13px;margin:8px 0 0;max-width:680px;line-height:1.5">{_esc(verdict_long)}</p>' if verdict_long else ""}
      {f'<p style="color:#94a3b8;font-size:12px;margin:6px 0 0;max-width:680px;line-height:1.5">{_esc(explanation)}</p>' if explanation else ""}
      <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px">
        {f'<span style="font-size:11px;color:#64748b">🕐 {_esc(date_str)}</span>' if date_str else ""}
        {f'<span style="font-size:11px;color:#64748b">👤 {_esc(executor)}</span>' if executor else ""}
        {f'<span style="font-size:11px;color:#64748b">🌐 {_esc(env_str[:80])}</span>' if env_str else ""}
      </div>
    </div>
  </div>
</div>

<!-- Content -->
<div style="max-width:1200px;margin:0 auto;padding:24px 28px">

{warnings_html}

{run_notes_html}

{totals_html}

{callouts_html}

{delta_html}

{ac_section_html}

<!-- TC summary table -->
<div style="background:#1e293b;border-radius:10px;overflow:hidden;border:1px solid #334155;margin-bottom:28px">
  <div style="padding:12px 16px;border-bottom:1px solid #334155;font-weight:700;font-size:13px">Test Cases</div>
  <table>
  <thead><tr><th style="width:120px">ID</th><th>Title</th><th style="width:120px">ACs</th><th style="width:100px">Status</th></tr></thead>
  <tbody>{tc_rows_html}</tbody>
  </table>
</div>

<!-- TC detail cards -->
<h3 style="color:#f1f5f9;margin:0 0 16px;font-size:15px">Test Case Evidence</h3>
{tc_detail_html}

{flat_shots_html}
{diffs_html}
{manual_html}

{next_html}

{env_layout_html}

{timing_html}

</div>

<div style="padding:20px 28px;color:#334155;font-size:11px;border-top:1px solid #1e293b;margin-top:20px">
  Generated by Agent Squad &nbsp;·&nbsp; {_esc(ticket_key)}/{_esc(run_name)}
</div>

<script>
function openLightbox(src) {{
  document.getElementById('lb-img').src = src;
  document.getElementById('lb').classList.add('open');
}}
function closeLightbox() {{
  document.getElementById('lb').classList.remove('open');
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeLightbox();
}});
</script>
</body>
</html>"""

    out_path = os.path.join(run_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    report_url = f"/evidence/{ticket_key}/runs/{run_name}/index.html"
    return True, f"Report generated ({len(html)//1024}KB)", report_url
