import asyncio
import json
import os
import subprocess
import uuid
import time

from typing import Any, Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from config import DEFAULT_PROJECT, PROJECTS, ENVIRONMENTS, EVIDENCE_DIR, STREAMS_DIR, STREAMS_RETENTION_DAYS, PIPELINE_DB, PIPELINE_RETENTION_DAYS
import otel as _otel
from jira_client import get_tickets, get_huddle_data, get_3x3_data
from agents import run_build, run_deploy, run_test, run_pipeline, watch_evidence, check_evidence, check_new_evidence, cleanup_env, generate_html_report
import bitbucket_client as bb
from chat import chat_stream
from streams import StreamRegistry, replay_events_from_disk, END_MARKER
from pipeline_store import PipelineStore


def _resolve_version():
    """Capture git SHA + dirty flag at startup. Process restarts bump this — that's the point."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    try:
        sha = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        dirty = bool(subprocess.check_output(
            ["git", "-C", repo_root, "status", "--porcelain"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip())
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return "unknown"


VERSION = _resolve_version()
STARTED_AT = time.time()

app = FastAPI(title="Agent Squad API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve evidence reports (index.html and embedded screenshots) under /evidence/<key>/runs/<run>/...
os.makedirs(EVIDENCE_DIR, exist_ok=True)
app.mount("/evidence", StaticFiles(directory=EVIDENCE_DIR), name="evidence")

streams = StreamRegistry(STREAMS_DIR)
streams.cleanup_old(STREAMS_RETENTION_DAYS)

# ── Pipeline state persistence ──
# Backed by SQLite (atomic UPSERTs, WAL mode) with an in-memory cache for
# fast sync reads (env-lock checks, /api/pipeline-states). The cache is a
# read-through copy of the DB; writes go through `pipeline_store.upsert`.
#
# `pipeline_states` exists as a module-level dict-like for backward-compat
# with the rest of this file — read access is `pipeline_states.get(...)`
# style and reflects the live cache. Mutations MUST go through
# `_update_pipeline_state` so the SQLite row stays in sync.
LEGACY_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "pipeline-state.json")
pipeline_store = PipelineStore(PIPELINE_DB)
pipeline_store.migrate_from_json(LEGACY_STATE_FILE)
pipeline_store.cleanup_old(PIPELINE_RETENTION_DAYS)
pipeline_states = pipeline_store.all_states()

import council
council.configure(streams, pipeline_store)

import auto_provision
auto_provision.pipeline_store = pipeline_store
auto_provision.streams_mod = streams


@app.on_event("startup")
async def _start_auto_provision_loop():
    asyncio.create_task(auto_provision.run_loop())
    asyncio.create_task(_parent_env_keepalive_loop())


async def _parent_env_keepalive_loop():
    """Renew the parent env's lease at startup and every keepalive interval.

    If we don't, the cheap PROJ env we clone from (qa-env-1 by default)
    will expire and Deploy will reclaim it — then every auto-provision
    fails at `deploycli create` with "parent not found"."""
    import sys
    import quartermaster
    from config import AUTO_PROVISION_PARENT_KEEPALIVE_INTERVAL_SEC
    while True:
        ok, msg = await quartermaster.renew_parent_env_lease()
        prefix = "parent-env-keepalive" if ok else "parent-env-keepalive FAILED"
        print(f"[{prefix}] {msg}", file=sys.stderr, flush=True)
        await asyncio.sleep(AUTO_PROVISION_PARENT_KEEPALIVE_INTERVAL_SEC)


def _update_pipeline_state(pipeline_id, updates):
    """Single entry point for mutating pipeline state. Atomic via SQLite UPSERT.

    Returns nothing — the in-memory `pipeline_states` dict is replaced with
    the store's view so the rest of the file sees fresh values immediately."""
    pipeline_store.upsert(pipeline_id, updates)
    # Cheap sync: the store keeps its own cache, so we just re-snapshot.
    pipeline_states.clear()
    pipeline_states.update(pipeline_store.all_states())


# ── Env locks ────────────────────────────────────────────────────────────
# One pipeline at a time per deploy env. Without this, two lanes can race
# to deploy snapshots to qa-env, each overwriting the other — wasting
# 40-min deploys and producing whichever snapshot won the race.
#
# Locks are in-process (single uvicorn worker is assumed). A stale lock is
# any lock whose holding pipeline's status is completed/failed or whose
# pipeline_state hasn't updated in STALE_ENV_LOCK_SECONDS — recovered on
# any acquire attempt so a crashed pipeline can't hold an env forever.

STALE_ENV_LOCK_SECONDS = 2 * 60 * 60  # 2 hours

env_locks: dict[str, str] = {}  # env_name -> pipeline_id holding it


def _env_lock_is_stale(env: str) -> bool:
    pipeline_id = env_locks.get(env)
    if not pipeline_id:
        return False
    state = pipeline_states.get(pipeline_id)
    if state is None:
        return True  # holder vanished — must be a crash or restart
    if state.get("status") in ("completed", "failed"):
        return True
    updated = state.get("updated_at") or 0
    if updated and (time.time() - updated) > STALE_ENV_LOCK_SECONDS:
        return True
    return False


def acquire_env_lock(env: str, pipeline_id: str) -> tuple[bool, str]:
    """Returns (acquired, holder_pipeline_id_or_empty)."""
    if not env:
        return True, ""
    current = env_locks.get(env)
    if current == pipeline_id:
        return True, pipeline_id  # idempotent
    if current and not _env_lock_is_stale(env):
        return False, current
    env_locks[env] = pipeline_id
    return True, pipeline_id


def release_env_lock(pipeline_id: str) -> None:
    for env, holder in list(env_locks.items()):
        if holder == pipeline_id:
            env_locks.pop(env, None)


def _reconcile_env_locks_from_states() -> None:
    """At startup, rebuild env_locks from persisted pipeline_states so a
    backend restart doesn't blow open env access for in-flight pipelines."""
    for pipeline_id, state in pipeline_states.items():
        if state.get("status") != "running":
            continue
        env = state.get("env")
        if env and env not in env_locks:
            env_locks[env] = pipeline_id


_reconcile_env_locks_from_states()


@app.get("/api/version")
async def api_version():
    """Backend version stamp. Frontend renders this in the header so stale-code
    surprises become visible instead of silent."""
    return {
        "version": VERSION,
        "startedAt": STARTED_AT,
        "uptimeSec": int(time.time() - STARTED_AT),
    }


@app.get("/api/environments")
async def api_environments():
    return ENVIRONMENTS


@app.get("/api/projects")
async def api_projects():
    """List of Jira project keys the dashboard knows about. Frontend fetches
    this so the project dropdown can't drift out of sync with the backend's
    PROJECTS allowlist."""
    return {"projects": PROJECTS, "default": DEFAULT_PROJECT}


@app.get("/api/env-locks")
async def api_env_locks():
    """Which envs are currently held by a running pipeline.

    Frontend uses this to disable env picker entries instead of letting
    the user submit and get a 409 back."""
    locks = {}
    for env, pipeline_id in env_locks.items():
        if _env_lock_is_stale(env):
            continue
        state = pipeline_states.get(pipeline_id, {})
        locks[env] = {
            "pipelineId": pipeline_id,
            "ticketKey": state.get("ticketKey", ""),
            "stage": state.get("stage", ""),
            "status": state.get("status", ""),
        }
    return locks


class ReleaseEnvRequest(BaseModel):
    pipelineId: str


@app.post("/api/release-env")
async def api_release_env(req: ReleaseEnvRequest):
    """Explicitly release the env held by a pipeline (e.g. lane dismiss).

    Idempotent — releasing a non-held pipeline is a no-op."""
    release_env_lock(req.pipelineId)
    return {"ok": True}


@app.get("/api/tickets")
async def api_tickets(project: str = Query(default=DEFAULT_PROJECT)):
    if project not in PROJECTS:
        return {"error": f"Unknown project: {project}"}
    tickets = await get_tickets(project)
    for t in tickets:
        t["evidence"] = check_evidence(t["key"])
    return tickets


@app.get("/api/dev-info/{key}")
async def api_dev_info(key: str):
    import httpx
    from config import JIRA_BASE_URL, REPO_LIST
    from jira_client import _headers, _get_dev_info
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{JIRA_BASE_URL}/rest/api/2/issue/{key}",
            params={"fields": "id"},
            headers=_headers(),
        )
        if resp.status_code != 200:
            return await _bb_dev_info_fallback(key)
        issue_id = resp.json()["id"]
    result = await _get_dev_info(issue_id)
    if not result:
        result = await _bb_dev_info_fallback(key)
    return result


async def _bb_dev_info_fallback(ticket_key: str) -> list:
    """Search Bitbucket directly for PRs referencing the ticket key across all known repos."""
    from config import REPO_LIST
    tasks = [bb.find_prs_for_ticket(repo, ticket_key) for repo in REPO_LIST]
    all_prs = await asyncio.gather(*tasks, return_exceptions=True)
    result = []
    for prs in all_prs:
        if isinstance(prs, Exception) or not prs:
            continue
        for pr in prs:
            source = pr.get("source", {}).get("branch", {}).get("name", "")
            dest = pr.get("destination", {}).get("branch", {}).get("name", "")
            repo_full = pr.get("source", {}).get("repository", {}).get("full_name", "")
            repo_name = repo_full.split("/")[-1] if repo_full else ""
            if repo_name and source:
                result.append({
                    "repo": repo_full or repo_name,
                    "branch": source,
                    "destBranch": dest,
                    "prStatus": (pr.get("state") or "").upper(),
                    "prId": str(pr.get("id", "")),
                    "source": "bitbucket",
                })
    return result


@app.get("/api/evidence/{key}")
async def api_evidence(key: str):
    return check_evidence(key)


@app.get("/api/evidence-history")
async def api_evidence_history():
    """Return evidence summary for every ticket dir in ~/evidence/, sorted newest-run first.

    Used by the Evidence History panel — shows all tested tickets regardless of
    whether their lane was dismissed.
    """
    if not os.path.isdir(EVIDENCE_DIR):
        return []
    results = []
    for entry in sorted(os.listdir(EVIDENCE_DIR)):
        ticket_dir = os.path.join(EVIDENCE_DIR, entry)
        if not os.path.isdir(ticket_dir):
            continue
        ev = check_evidence(entry)
        if ev["status"] == "none":
            continue
        # Find newest run mtime so we can sort correctly
        runs_path = os.path.join(ticket_dir, "runs")
        latest_mtime = 0
        if os.path.isdir(runs_path):
            for run in os.listdir(runs_path):
                rpath = os.path.join(runs_path, run)
                if os.path.isdir(rpath):
                    mt = os.path.getmtime(rpath)
                    if mt > latest_mtime:
                        latest_mtime = mt
        results.append({
            "key": entry,
            "status": ev["status"],
            "score": ev["score"],
            "time": ev["time"],
            "reportUrl": ev["reportUrl"],
            "needsReport": ev.get("needsReport", False),
            "latestRun": ev.get("latestRun", ""),
            "latestMtime": latest_mtime,
            "claudeCost": ev.get("claudeCost"),
        })
    results.sort(key=lambda r: r["latestMtime"], reverse=True)
    return results


# ── OTEL / cost endpoints ──────────────────────────────────────────────────

@app.get("/api/otel/status")
async def api_otel_status():
    """Check if local OTEL telemetry is available and return per-ticket cost summary."""
    available = _otel.is_available()
    if not available:
        return {"available": False, "tickets": []}

    tickets = []
    if os.path.isdir(EVIDENCE_DIR):
        for entry in sorted(os.listdir(EVIDENCE_DIR)):
            runs_path = os.path.join(EVIDENCE_DIR, entry, "runs")
            costs = _otel.costs_for_ticket(runs_path)
            if costs:
                tickets.append({
                    "key": entry,
                    "totalCost": round(sum(costs.values()), 4),
                    "runs": [{"run": k, "cost": v} for k, v in sorted(costs.items())],
                })
    return {"available": True, "tickets": tickets}


# ── Bitbucket Cloud endpoints ─────────────────────────────────────────────

@app.get("/api/bb/auth")
async def api_bb_auth():
    """Check if Bitbucket credentials are configured and valid."""
    ok = await bb.check_auth()
    return {"authenticated": ok}


@app.get("/api/bb/pr/{repo}/{pr_id}")
async def api_bb_pr(repo: str, pr_id: str):
    """Fetch a single PR from Bitbucket Cloud."""
    try:
        data = await bb.get_pr(repo, pr_id)
        return data
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/api/bb/pr/{repo}/{pr_id}/diff")
async def api_bb_pr_diff(repo: str, pr_id: str):
    """Return unified diff text for a PR."""
    diff = await bb.get_pr_diff(repo, pr_id)
    if not diff:
        return JSONResponse(status_code=404, content={"error": "diff not available"})
    return {"diff": diff}


@app.get("/api/bb/pr/{repo}/{pr_id}/diffstat")
async def api_bb_pr_diffstat(repo: str, pr_id: str):
    """Return file-level change stats for a PR."""
    return await bb.get_pr_diffstat(repo, pr_id)


@app.get("/api/bb/prs/{ticket_key}")
async def api_bb_prs_for_ticket(ticket_key: str):
    """Search all known repos for PRs referencing the ticket key."""
    return await _bb_dev_info_fallback(ticket_key)


class BBCommentRequest(BaseModel):
    message: str


@app.post("/api/bb/pr/{repo}/{pr_id}/comment")
async def api_bb_pr_comment(repo: str, pr_id: str, req: BBCommentRequest):
    """Post a comment on a Bitbucket PR."""
    ok = await bb.post_pr_comment(repo, pr_id, req.message)
    return {"ok": ok}


# ── Evidence check ────────────────────────────────────────────────────────

class CheckEvidenceRequest(BaseModel):
    baseline_runs: List[str] = []


@app.post("/api/check-evidence/{key}")
async def api_check_evidence(key: str, req: CheckEvidenceRequest):
    """On-demand check for new evidence. Called by retry button.

    When new evidence is found, fire the Council Review Gate: gather PR refs
    for the ticket, start the council reviewers, and return the council
    stream id so the frontend can subscribe and render the verdict inline.
    """
    result = check_new_evidence(key, req.baseline_runs)
    if not result.get("found"):
        return result

    # Find the pipeline for this ticket (latest running/most recently updated)
    pipeline_id = None
    best_updated = -1.0
    for pid, state in pipeline_states.items():
        if state.get("ticketKey") != key:
            continue
        updated = state.get("updated_at") or 0
        if updated >= best_updated:
            best_updated = updated
            pipeline_id = pid

    if not pipeline_id:
        # No lane to attach to — return evidence result without council.
        return result

    try:
        pr_refs = await _gather_pr_refs(key)
    except Exception:
        pr_refs = []

    run_name = result.get("latestRun") or ""
    council_stream_id = council.start(
        ticket_key=key,
        run_name=run_name,
        pipeline_id=pipeline_id,
        pr_refs=pr_refs,
    )
    return {**result, "awaitingCouncil": True, "councilStreamId": council_stream_id}



async def _gather_pr_refs(ticket_key: str) -> List[dict]:
    """Resolve a ticket key to PR refs in the shape council reviewers expect.

    Returns a list of {repo, pr_id, title} dicts. Empty on any failure so
    the council can still run (code-reviewer will PASS when there are no PRs).
    """
    import httpx
    from config import JIRA_BASE_URL
    from jira_client import _headers, _get_dev_info
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{JIRA_BASE_URL}/rest/api/2/issue/{ticket_key}",
                params={"fields": "id,summary"},
                headers=_headers(),
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            issue_id = data["id"]
            title = (data.get("fields") or {}).get("summary") or ""
    except Exception:
        return []

    prs = await _get_dev_info(issue_id) or []
    refs: List[dict] = []
    for pr in prs:
        repo = pr.get("repo") or ""
        pr_id = pr.get("prId") or ""
        if not repo or not pr_id:
            continue
        repo_short = repo.split("/")[-1] if "/" in repo else repo
        refs.append({"repo": repo_short, "pr_id": str(pr_id), "title": title})
    return refs


class CouncilOverrideRequest(BaseModel):
    reason: str


@app.post("/api/council/override/{pipeline_id}")
async def api_council_override(pipeline_id: str, req: CouncilOverrideRequest):
    try:
        payload = await council.override(pipeline_id, req.reason, user=os.environ.get("USER", "unknown"))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ok": True, "override": payload}


@app.get("/api/council/{pipeline_id}")
async def api_council_get(pipeline_id: str):
    state = pipeline_store.get(pipeline_id)
    if not state:
        return JSONResponse(status_code=404, content={"error": "pipeline not found"})
    return {
        "councilStatus": state.get("councilStatus"),
        "councilPayload": state.get("councilPayload"),
        "councilOverride": state.get("councilOverride"),
    }


class GenerateReportRequest(BaseModel):
    run_name: str = ""


@app.post("/api/generate-report/{key}")
async def api_generate_report(key: str, req: GenerateReportRequest):
    """Generate (or regenerate) index.html for the latest run.

    Embeds summary, TC results, screenshots (base64), markups, and diffs.
    Returns the report URL on success so the UI can open it immediately.
    """
    run_name = req.run_name or None
    success, message, report_url = generate_html_report(key, run_name)
    return {"success": success, "message": message, "reportUrl": report_url}


class CheckDeployRequest(BaseModel):
    env: str
    services: List[dict] = []  # [{service, snapshot}, ...]


async def _check_deploy_core(env: str, services: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Core check-deploy logic, extracted for reuse (e.g., Builder fast-path).

    Takes env name and a list of {service, snapshot} dicts.
    Returns {"allDeployed": bool, "anyFailed": bool, "services": [...]}.
    Behaviour is identical to the original api_check_deploy body.
    """
    from agents import check_snapshot, _snapshot_matches
    # Group snapshots by service
    service_snapshots: Dict[str, List[str]] = {}
    for svc in services:
        service_snapshots.setdefault(svc["service"], []).append(svc["snapshot"])

    results = []
    for service, snapshots in service_snapshots.items():
        # Check if any of the snapshots are deployed (healthy) on this service
        deployed = False
        matched_snap = ""
        version = ""
        url = ""
        last_status: Dict[str, Any] = {"status": "", "buildStatus": "", "scaleCurrent": 0, "scaleTarget": 0, "healthy": False, "buildUrl": ""}
        failed_unhealthy = False
        for snap in snapshots:
            exists, ver, u, status_info = await check_snapshot(env, service, snap)
            version = ver
            url = u
            last_status = status_info
            if exists:
                deployed = True
                matched_snap = snap
                break
            if _snapshot_matches(snap, ver or "") and status_info["status"] in ("FAILED", "UNSTABLE", "ERROR"):
                failed_unhealthy = True
                matched_snap = snap
                break
        # Infer a human-readable failure reason from the Deploy status fields.
        failure_reason = ""
        if failed_unhealthy:
            bs = last_status.get("buildStatus", "")
            st = last_status.get("status", "")
            sc = last_status.get("scaleCurrent", 0)
            if bs == "FAILURE":
                failure_reason = "Jenkins build failed — likely a missing dependency or config error. Check the build log."
            elif bs == "ABORTED":
                failure_reason = "Jenkins build was aborted. A conflicting deploy may have restarted the rollout."
            elif st == "FAILED" and sc == 0:
                failure_reason = "Pods crashed on startup — possible missing env config or incompatible dependency version."
            elif st == "UNSTABLE":
                failure_reason = "Deploy is unstable — pods may be crash-looping. Check Deploy UI for pod logs."
            else:
                failure_reason = f"Deploy is in {st} state (buildStatus={bs}). Check Deploy UI for details."
        results.append({
            "service": service,
            "snapshot": matched_snap or snapshots[-1],
            "deployed": deployed,
            "failed": failed_unhealthy,
            "failureReason": failure_reason,
            "currentVersion": version,
            "url": url,
            "status": last_status["status"],
            "buildStatus": last_status["buildStatus"],
            "scaleCurrent": last_status["scaleCurrent"],
            "scaleTarget": last_status["scaleTarget"],
            "buildUrl": last_status.get("buildUrl", ""),
        })
    all_deployed = all(r["deployed"] for r in results)
    any_failed = any(r["failed"] for r in results)
    return {"allDeployed": all_deployed, "anyFailed": any_failed, "services": results}


@app.post("/api/check-deploy")
async def api_check_deploy(req: CheckDeployRequest):
    """On-demand check if snapshots are deployed. Returns status per service.
    For services with multiple snapshots, any matching snapshot counts as deployed."""
    return await _check_deploy_core(req.env, req.services)


class RunCommandRequest(BaseModel):
    command: str


@app.post("/api/run-command")
async def api_run_command(req: RunCommandRequest):
    """Run an deploycli command and return output. Only allows deploycli commands."""
    cmd = req.command.strip()
    if not cmd.startswith("deploycli "):
        return {"error": "Only deploycli commands are allowed", "exit_code": 1, "output": []}
    from agents import _run_cmd_and_capture
    code, lines = await _run_cmd_and_capture(cmd)
    return {"exit_code": code, "output": lines}


class CleanupEnvRequest(BaseModel):
    env: str
    keep: List[str] = []


@app.post("/api/cleanup-env")
async def api_cleanup_env(req: CleanupEnvRequest):
    """Reset every snapshot service on the env to its stable reference,
    except those in keep[]. Streams progress via SSE."""
    if req.env not in ENVIRONMENTS:
        return {"error": f"Unknown env: {req.env}"}

    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, cleanup_env(req.env, req.keep)))
    return {"streamId": stream_id}


@app.post("/api/auto-provision/retry/{ticket_key}")
async def api_auto_provision_retry(ticket_key: str):
    auto_provision.reset_failures(ticket_key)
    try:
        stream_id = await auto_provision.start_quartermaster_pipeline(ticket_key)
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"error": str(e), "ticketKey": ticket_key})
    return {"streamId": stream_id, "ticketKey": ticket_key}


class BuildRequest(BaseModel):
    repo: str
    branch: str


class DeployRequest(BaseModel):
    env: str = ""
    service: str = ""
    snapshot: str = ""


class TestRequest(BaseModel):
    ticketKey: str
    envUrl: str


class PipelineRequest(BaseModel):
    repo: str
    branch: str
    env: str = ""
    service: str = ""
    snapshot: str = ""
    ticketKey: str
    envUrl: str = ""


@app.post("/api/build")
async def api_build(req: BuildRequest):
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, run_build(req.repo, req.branch)))
    return {"streamId": stream_id}


@app.post("/api/deploy")
async def api_deploy(req: DeployRequest):
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, run_deploy(req.env, req.service, req.snapshot)))
    return {"streamId": stream_id}


@app.post("/api/test")
async def api_test(req: TestRequest):
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, run_test(req.ticketKey, req.envUrl)))
    return {"streamId": stream_id}


class ChatSendRequest(BaseModel):
    message: str
    session_id: str = ""


@app.post("/api/chat/send")
async def api_chat_send(req: ChatSendRequest):
    """Send a message to Claude Code (`claude -p`) and stream the response.
    Pass session_id to continue a prior conversation."""
    stream_id = str(uuid.uuid4())
    streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, chat_stream(req.message, req.session_id or None)))
    return {"streamId": stream_id}


@app.post("/api/pipeline")
async def api_pipeline(req: PipelineRequest):
    """Full pipeline: build → deploy → test → evidence. Single SSE stream.

    Acquires an exclusive lock on `req.env` for the lifetime of the pipeline.
    Two lanes attempting the same env at once is the bug class this guards —
    one ticket's snapshot overwriting another's mid-deploy wasted 40 minutes
    of build time and produced unpredictable results.
    """
    pipeline_id = str(uuid.uuid4())
    stream_id = pipeline_id

    acquired, holder = acquire_env_lock(req.env, pipeline_id)
    if not acquired:
        holder_state = pipeline_states.get(holder, {})
        return JSONResponse(
            status_code=409,
            content={
                "error": "env_in_use",
                "env": req.env,
                "heldBy": {
                    "pipelineId": holder,
                    "ticketKey": holder_state.get("ticketKey", ""),
                    "stage": holder_state.get("stage", ""),
                    "status": holder_state.get("status", ""),
                },
                "message": (
                    f"Env {req.env} is in use by pipeline for "
                    f"{holder_state.get('ticketKey') or holder} "
                    f"({holder_state.get('stage') or 'running'}). "
                    f"Pick another env, wait for that one to finish, or dismiss "
                    f"the lane to release the lock."
                ),
            },
        )

    streams.create(stream_id)

    # Save initial state
    _update_pipeline_state(pipeline_id, {
        "ticketKey": req.ticketKey,
        "env": req.env,
        "repo": req.repo,
        "branch": req.branch,
        "service": req.service,
        "snapshot": req.snapshot,
        "envUrl": req.envUrl,
        "stage": "builder",
        "status": "running",
        "logs": [],
    })

    # Wrap pipeline to track state + release env lock on terminal event.
    async def _tracked_pipeline():
        try:
            async for event in run_pipeline(
                repo=req.repo, branch=req.branch, env=req.env,
                service=req.service, snapshot=req.snapshot,
                ticket_key=req.ticketKey, env_url=req.envUrl,
            ):
                # Track stage changes
                if event.get("type") == "stage_change":
                    _update_pipeline_state(pipeline_id, {"stage": event["stage"]})
                elif event.get("type") == "done":
                    status = "completed" if event.get("success") else "failed"
                    _update_pipeline_state(pipeline_id, {"status": status})
                # Keep last 50 log lines
                if event.get("type") == "log":
                    state = pipeline_states.get(pipeline_id, {})
                    logs = state.get("logs", [])
                    logs.append(event.get("data", ""))
                    _update_pipeline_state(pipeline_id, {"logs": logs[-50:]})
                yield event
        finally:
            release_env_lock(pipeline_id)

    asyncio.create_task(_run_stream(stream_id, _tracked_pipeline()))
    return {"streamId": stream_id, "pipelineId": pipeline_id}


@app.get("/api/pipeline-states")
async def api_pipeline_states():
    """Get all active/recent pipeline states for resuming after refresh."""
    # Return pipelines that are still running or completed recently (last hour)
    cutoff = time.time() - 3600
    active = {
        k: v for k, v in pipeline_states.items()
        if v.get("updated_at", 0) > cutoff
    }
    return active


@app.post("/api/pipeline/resume/{pipeline_id}")
async def api_resume_pipeline(pipeline_id: str):
    """Resume a pipeline from where it left off."""
    state = pipeline_states.get(pipeline_id)
    if not state:
        return {"error": "Pipeline not found"}

    if state.get("status") == "completed":
        return {"error": "Pipeline already completed"}

    if state.get("councilStatus") == "pending":
        from agents import check_new_evidence
        ticket_key = state.get("ticketKey", "")
        ev = check_new_evidence(ticket_key, state.get("baselineRuns") or []) or {}
        run_name = ev.get("run", "") if isinstance(ev, dict) else ""
        if run_name:
            try:
                pr_refs = await _gather_pr_refs(ticket_key)
            except Exception:
                pr_refs = []
            council_stream_id = council.start(
                ticket_key=ticket_key,
                run_name=run_name,
                pipeline_id=pipeline_id,
                pr_refs=pr_refs,
            )
            return {"resumedCouncil": True, "pipelineId": pipeline_id, "councilStreamId": council_stream_id}

    last_stage = state.get("stage", "builder")
    ticket_key = state.get("ticketKey", "")
    env = state.get("env", "")
    env_url = state.get("envUrl", "")

    stream_id = str(uuid.uuid4())
    streams.create(stream_id)

    _update_pipeline_state(pipeline_id, {"status": "running"})

    # Resume from the last stage
    async def _resume():
        stages = ["builder", "shipper", "inspector", "scribe"]
        start_idx = stages.index(last_stage) if last_stage in stages else 0

        yield {"type": "log", "data": f"=== Resuming {ticket_key} from {last_stage} ==="}

        if start_idx <= 0:
            # Need full pipeline
            async for event in run_pipeline(
                repo=state.get("repo", ""), branch=state.get("branch", ""),
                env=env, service=state.get("service", ""),
                snapshot=state.get("snapshot", ""),
                ticket_key=ticket_key, env_url=env_url,
            ):
                if event.get("type") == "stage_change":
                    _update_pipeline_state(pipeline_id, {"stage": event["stage"]})
                elif event.get("type") == "done":
                    status = "completed" if event.get("success") else "failed"
                    _update_pipeline_state(pipeline_id, {"status": status})
                yield event
        elif start_idx <= 2:
            # Resume from inspector (test) — build & deploy already done.
            # run_test ends with done waiting_for_evidence; user clicks "Check Evidence" to advance.
            yield {"type": "stage_change", "stage": "inspector"}
            _update_pipeline_state(pipeline_id, {"stage": "inspector"})
            yield {"type": "log", "data": f"[Inspector] Resuming — build & deploy already done"}
            async for event in run_test(ticket_key, env_url):
                if event.get("type") == "done":
                    status = "completed" if event.get("success") else "failed"
                    _update_pipeline_state(pipeline_id, {"status": status})
                yield event

    asyncio.create_task(_run_stream(stream_id, _resume()))
    return {"streamId": stream_id, "pipelineId": pipeline_id, "resumedFrom": last_stage}


@app.get("/api/watch-evidence/{key}")
async def api_watch_evidence(key: str):
    """SSE stream that pushes updates when evidence changes for a ticket.

    Unlike other endpoints, this one inlines the generator since the
    consumer connects right here — no need for the replay-from-disk path.
    Still uses the Stream API so a reload survives the watcher.
    """
    stream_id = str(uuid.uuid4())
    stream = streams.create(stream_id)
    asyncio.create_task(_run_stream(stream_id, watch_evidence(key)))

    async def event_generator():
        sub = stream.subscribe()
        try:
            while True:
                event = await sub.get()
                if event is None or event.get("type") == END_MARKER:
                    break
                yield {"data": json.dumps(event)}
        finally:
            stream.unsubscribe(sub)

    return EventSourceResponse(event_generator())


async def _run_stream(stream_id, generator):
    stream = streams.get(stream_id)
    if stream is None:
        # The endpoint that started this task should have already called
        # streams.create() — if not, the events just go nowhere. Bail.
        return
    try:
        async for event in generator:
            stream.append(event)
    except Exception as e:
        stream.append({"type": "error", "msg": str(e)})
    finally:
        stream.end()


@app.get("/api/stream/{stream_id}")
async def api_stream(stream_id: str):
    """Replay the on-disk event log, then tail any live updates.

    This is the key restart-survival mechanism: after a backend reload the
    in-memory Stream is gone but the .jsonl file is still on disk. The
    frontend's EventSource auto-reconnects on the error, hits this endpoint
    again, and gets the full history replayed — no lost progress.
    """
    if not streams.exists_on_disk(stream_id):
        return {"error": "Stream not found"}

    async def event_generator():
        # Subscribe BEFORE reading disk so we capture anything written
        # between the disk read and the tail start. Dedup by `_seq`.
        live = streams.get(stream_id)
        sub_queue = live.subscribe() if (live is not None and not live.ended) else None

        try:
            max_seq = 0
            ended_on_disk = False
            for event, seq in replay_events_from_disk(streams.path_for(stream_id)):
                if event.get("type") == END_MARKER:
                    ended_on_disk = True
                    break
                yield {"data": json.dumps(event)}
                if seq > max_seq:
                    max_seq = seq

            # Stream already finished — nothing more to send.
            if ended_on_disk or sub_queue is None or live is None or live.ended:
                return

            while True:
                try:
                    event = await asyncio.wait_for(sub_queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"type": "ping"})}
                    continue
                if event is None or event.get("type") == END_MARKER:
                    break
                seq = event.get("_seq", 0)
                if seq <= max_seq:
                    continue
                yield {"data": json.dumps(event)}
                max_seq = seq
        finally:
            if sub_queue is not None and live is not None:
                live.unsubscribe(sub_queue)

    return EventSourceResponse(event_generator())


class ReportRequest(BaseModel):
    project: str = DEFAULT_PROJECT
    notes: str = ""


@app.post("/api/huddle")
async def api_huddle(req: ReportRequest):
    text = await get_huddle_data(req.project, req.notes)
    return {"text": text}


@app.post("/api/3x3")
async def api_3x3(req: ReportRequest):
    text = await get_3x3_data(req.project, req.notes)
    return {"text": text}


# Keep GET for backward compat
@app.get("/api/huddle")
async def api_huddle_get(project: str = Query(default=DEFAULT_PROJECT)):
    text = await get_huddle_data(project)
    return {"text": text}


@app.get("/api/3x3")
async def api_3x3_get(project: str = Query(default=DEFAULT_PROJECT)):
    text = await get_3x3_data(project)
    return {"text": text}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
