"""
Auto-provision on Ready for QA.

Drives a periodic poll that detects newcomer tickets entering "Ready for QA"
and spawns a Quartermaster pipeline to provision a Deploy env + snapshots
for each one.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import httpx

logger = logging.getLogger(__name__)

import quartermaster
from jira_client import _get_dev_info, _headers, get_tickets

if TYPE_CHECKING:
    from streams import StreamRegistry
    from pipeline_store import PipelineStore

from config import (
    AUTO_PROVISION_ENABLED,
    AUTO_PROVISION_LEASE_HOURS,
    AUTO_PROVISION_MAX_FAILURES,
    AUTO_PROVISION_POLL_SEC,
    DEFAULT_PROJECT,
)

# Reconciliation cadence: every 5th poll (~5min when poll is 60s) we walk the
# full Ready-for-QA list, not just newcomers. Newcomer diffs catch tickets that
# enter status while the backend is alive; reconciliation catches existing
# backlog and recovers from any state we missed (backend restart, transient
# failures, etc.).
RECONCILE_EVERY_N_POLLS = 5

# Lease renewal threshold: if a ready env has more than this much time on its
# current lease, we leave it alone. Below the threshold we issue a single
# `deploycli renew` to keep QA from losing the env mid-test.
LEASE_RENEW_THRESHOLD_SECONDS = 12 * 60 * 60


def tick(prev_ready_set: Optional[Set[str]], current_ready_set: Set[str]) -> Set[str]:
    """
    Return the set of ticket keys newly entering Ready for QA since the last poll.

    On first call after backend startup (prev_ready_set is None), returns an empty
    set so we don't stampede every Ready-for-QA ticket on boot.
    """
    if prev_ready_set is None:
        return set()
    return current_ready_set - prev_ready_set


_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(ticket_key: str) -> asyncio.Lock:
    """Return (and lazily create) a per-ticket asyncio.Lock."""
    lock = _locks.get(ticket_key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[ticket_key] = lock
    return lock


# Mutated only from the asyncio event loop (no executor offloading planned);
# no lock needed. Same applies to _locks above.
_failures: Dict[str, int] = {}


def record_failure(ticket_key: str) -> None:
    """Increment the failure counter for a ticket."""
    _failures[ticket_key] = _failures.get(ticket_key, 0) + 1


def record_success(ticket_key: str) -> None:
    """Clear the failure counter for a ticket after a successful provision."""
    _failures.pop(ticket_key, None)


def get_failure_count(ticket_key: str) -> int:
    """Return the current failure count for a ticket (0 if never failed)."""
    return _failures.get(ticket_key, 0)


def should_retry(ticket_key: str) -> bool:
    """
    Return True if the ticket should be retried after a failure.

    Semantics: returns True while failure_count < AUTO_PROVISION_MAX_FAILURES.
    With MAX_FAILURES=2: the first failure (count=1) still retries; the second
    failure (count=2) blocks further auto-retries. Manual retry via the UI
    resets the counter.
    """
    return get_failure_count(ticket_key) < AUTO_PROVISION_MAX_FAILURES


def reset_failures(ticket_key: str) -> None:
    """Manually clear the failure counter (e.g. after a manual retry)."""
    _failures.pop(ticket_key, None)


# Module-level handles, set by server.py at startup, patched in tests
pipeline_store: Optional["PipelineStore"] = None
streams_mod: Optional["StreamRegistry"] = None


async def _gather_prs(ticket_key: str) -> List[Dict[str, Any]]:
    """
    Build the [{repo, branch, snapshot}] list for a ticket by querying Jira
    dev-info directly (same pattern as agents.run_pipeline).
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://acme.atlassian.net/rest/api/2/issue/{ticket_key}",
                params={"fields": "id"},
                headers=_headers(),
            )
            issue_id = resp.json()["id"] if resp.status_code == 200 else None
        dev_info = await _get_dev_info(issue_id) if issue_id else []
    except Exception:
        dev_info = []

    out: List[Dict[str, Any]] = []
    for pr in dev_info or []:
        repo = pr.get("repo")
        branch = pr.get("branch")
        if not repo or not branch:
            continue
        # Jira dev-info returns repo as "acme/<slug>" but downstream consumers
        # (bb.get_file, agents.run_build) want the bare slug — bb.get_file prepends
        # the workspace itself, so an org-prefixed slug yields a 404 manifest lookup
        # and the PR is silently dropped as "no manifest".
        if "/" in repo:
            repo = repo.split("/", 1)[1]
        snapshot = branch.upper().replace("/", "-")
        out.append({"repo": repo, "branch": branch, "snapshot": snapshot})
    return out


async def start_quartermaster_pipeline(ticket_key: str) -> str:
    """
    Create pipeline-state, open stream, spawn background provision task.
    Returns the streamId.
    """
    if streams_mod is None or pipeline_store is None:
        raise RuntimeError(
            "auto_provision module handles not initialized — "
            "server.py must set streams_mod and pipeline_store at startup"
        )

    env_name = ticket_key.lower()
    pipeline_id = f"qm-{ticket_key}-{uuid.uuid4().hex[:8]}"
    stream = streams_mod.create(pipeline_id)
    stream_id = stream.id

    pipeline_store.upsert(pipeline_id, {
        "pipelineId": pipeline_id,
        "ticketKey": ticket_key,
        "env": env_name,
        "stage": "quartermaster",
        "status": "running",
        "streamId": stream_id,
        "logs": [],
    })

    async def _run():
        try:
            async with _lock_for(ticket_key):
                prs = await _gather_prs(ticket_key)
                result = await quartermaster.provision_env(ticket_key, prs, stream)
                status = "ready_for_qa" if result["status"] == "ok" else "failed"
                updates = {
                    "pipelineId": pipeline_id,
                    "ticketKey": ticket_key,
                    "env": env_name,
                    "stage": "quartermaster",
                    "status": status,
                }
                if result["status"] == "failed":
                    updates["failureStep"] = result["step"]
                    updates["failureReason"] = result["reason"]
                    record_failure(ticket_key)
                    updates["provisionFailures"] = get_failure_count(ticket_key)
                    updates["provisionBlocked"] = not should_retry(ticket_key)
                else:
                    record_success(ticket_key)
                    updates["provisionFailures"] = 0
                    updates["provisionBlocked"] = False
                pipeline_store.upsert(pipeline_id, updates)
        except Exception as e:
            record_failure(ticket_key)
            pipeline_store.upsert(pipeline_id, {
                "pipelineId": pipeline_id,
                "ticketKey": ticket_key,
                "env": env_name,
                "stage": "quartermaster",
                "status": "failed",
                "failureStep": "exception",
                "failureReason": str(e),
                "provisionFailures": get_failure_count(ticket_key),
                "provisionBlocked": not should_retry(ticket_key),
            })
        finally:
            stream.end()

    asyncio.create_task(_run())
    return stream_id


async def _fetch_ready_for_qa() -> List[Dict[str, Any]]:
    """Fetch tickets and filter to Ready for QA."""
    tickets = await get_tickets(DEFAULT_PROJECT)
    return [t for t in tickets if t.get("status") == "Ready for QA"]


async def _do_poll() -> None:
    """Single poll: fetch tickets, diff against persisted prev_ready_set, kick off newcomers."""
    if pipeline_store is None:
        logger.warning("auto_provision: pipeline_store not initialized — skipping poll")
        return

    try:
        tickets = await _fetch_ready_for_qa()
    except Exception:
        logger.warning("auto_provision: _fetch_ready_for_qa failed", exc_info=True)
        return

    current = {t["key"] for t in tickets}
    prev_raw = pipeline_store.get_meta("prev_ready_set", default=None)
    prev = set(prev_raw) if prev_raw is not None else None

    newcomers = tick(prev, current)
    for key in newcomers:
        if not should_retry(key):
            continue
        try:
            await start_quartermaster_pipeline(key)
        except Exception:
            logger.warning("auto_provision: start_quartermaster_pipeline(%s) failed", key, exc_info=True)
            record_failure(key)

    pipeline_store.set_meta("prev_ready_set", sorted(current))


async def _renew_lease(env_name: str) -> bool:
    """Renew the env's lease to AUTO_PROVISION_LEASE_HOURS. Returns True on success."""
    proc = await asyncio.create_subprocess_exec(
        "deploycli", "deploy", "renew",
        "--env", env_name,
        "--hour", str(AUTO_PROVISION_LEASE_HOURS),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def reconcile() -> None:
    """Walk every Ready-for-QA ticket and provision whatever's missing.

    Cadence: invoked every RECONCILE_EVERY_N_POLLS polls from run_loop (the
    newcomer diff still runs every poll). Sequential by design — one
    pipeline kicked off per reconcile tick — so the backlog drains over
    several cycles instead of stampeding deploy.

    Per ticket:
      - skip if a pipeline is already in flight (lock held)
      - skip if failure cap reached (should_retry == False)
      - if env+snapshots are ready: renew lease only when <12h remain
      - if not ready: kick off start_quartermaster_pipeline and STOP (one
        provisioning per tick to keep deploycli load bounded)
    """
    if pipeline_store is None:
        logger.warning("auto_provision: pipeline_store not initialized — skipping reconcile")
        return

    try:
        tickets = await _fetch_ready_for_qa()
    except Exception:
        logger.warning("auto_provision: _fetch_ready_for_qa failed in reconcile", exc_info=True)
        return

    for ticket in tickets:
        key = ticket["key"]

        lock = _lock_for(key)
        if lock.locked():
            continue
        if not should_retry(key):
            continue

        try:
            prs = await _gather_prs(key)
        except Exception:
            logger.warning("auto_provision: _gather_prs(%s) failed", key, exc_info=True)
            continue

        try:
            is_ready, expiration_ts = await quartermaster.is_env_ready_for_qa(key, prs)
        except Exception:
            logger.warning("auto_provision: is_env_ready_for_qa(%s) raised", key, exc_info=True)
            continue

        if is_ready:
            if expiration_ts is None:
                continue
            remaining = expiration_ts - int(time.time())
            if remaining < LEASE_RENEW_THRESHOLD_SECONDS:
                logger.info("auto_provision: renewing lease for %s (%ds remaining)", key, remaining)
                try:
                    await _renew_lease(key.lower())
                except Exception:
                    logger.warning("auto_provision: _renew_lease(%s) failed", key, exc_info=True)
            continue

        logger.info("auto_provision: reconcile kicking provisioner for %s", key)
        try:
            await start_quartermaster_pipeline(key)
        except Exception:
            logger.warning("auto_provision: start_quartermaster_pipeline(%s) failed in reconcile", key, exc_info=True)
            record_failure(key)
        # Sequential: one pipeline per reconcile tick.
        return


async def run_loop() -> None:
    """Forever-running poll loop. Spawned by server.py at startup.

    Every poll runs the newcomer diff (`_do_poll`). Every RECONCILE_EVERY_N_POLLS
    polls we also run `reconcile()` to walk the full Ready-for-QA backlog so
    existing tickets that the newcomer diff would miss (backend restart, prior
    failures, etc.) get caught up.

    CancelledError is intentionally allowed to propagate (FastAPI shutdown).
    All other exceptions are caught so a single bad poll cycle doesn't kill
    the poller for the lifetime of the backend process.
    """
    if not AUTO_PROVISION_ENABLED:
        return
    counter = 0
    while True:
        try:
            await _do_poll()
        except Exception:
            logger.exception("auto_provision: _do_poll raised unexpectedly")
        counter += 1
        if counter % RECONCILE_EVERY_N_POLLS == 0:
            try:
                await reconcile()
            except Exception:
                logger.exception("auto_provision: reconcile raised unexpectedly")
        await asyncio.sleep(AUTO_PROVISION_POLL_SEC)
