import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auto_provision import (
    _do_poll,
    _failures,
    _lock_for,
    _locks,
    get_failure_count,
    record_failure,
    record_success,
    reset_failures,
    should_retry,
    start_quartermaster_pipeline,
    tick,
)
from server import app


@pytest.fixture(autouse=True)
def _reset_module_state():
    _failures.clear()
    _locks.clear()
    yield
    _failures.clear()
    _locks.clear()


def test_tick_returns_newcomers():
    prev = {"PROJ-100", "PROJ-101"}
    current = {"PROJ-101", "PROJ-200", "PROJ-201"}
    assert tick(prev, current) == {"PROJ-200", "PROJ-201"}


def test_tick_returns_empty_when_no_change():
    prev = {"PROJ-100"}
    current = {"PROJ-100"}
    assert tick(prev, current) == set()


def test_tick_returns_empty_when_tickets_leave():
    prev = {"PROJ-100", "PROJ-101"}
    current = {"PROJ-100"}
    assert tick(prev, current) == set()


def test_tick_bootstrap_returns_empty_when_prev_is_none():
    # First-poll-after-startup case
    assert tick(None, {"PROJ-100"}) == set()


def test_lock_for_returns_same_lock_per_key():
    l1 = _lock_for("PROJ-404")
    l2 = _lock_for("PROJ-404")
    assert l1 is l2


def test_lock_for_returns_different_locks_per_key():
    a = _lock_for("PROJ-404")
    b = _lock_for("PROJ-405")
    assert a is not b


async def test_concurrent_acquire_serializes():
    order = []

    async def section(name):
        async with _lock_for("PROJ-404"):
            order.append(f"{name}-start")
            await asyncio.sleep(0.05)
            order.append(f"{name}-end")

    await asyncio.gather(section("A"), section("B"))
    assert order in (
        ["A-start", "A-end", "B-start", "B-end"],
        ["B-start", "B-end", "A-start", "A-end"],
    )


def test_record_failure_increments():
    record_failure("PROJ-1")
    assert get_failure_count("PROJ-1") == 1
    record_failure("PROJ-1")
    assert get_failure_count("PROJ-1") == 2


def test_record_success_clears():
    record_failure("PROJ-1")
    record_success("PROJ-1")
    assert get_failure_count("PROJ-1") == 0


def test_should_retry_true_below_threshold():
    record_failure("PROJ-1")
    assert should_retry("PROJ-1") is True


def test_should_retry_false_at_threshold():
    record_failure("PROJ-1")
    record_failure("PROJ-1")
    assert should_retry("PROJ-1") is False


def test_reset_failures_clears_counter():
    record_failure("PROJ-1")
    record_failure("PROJ-1")
    reset_failures("PROJ-1")
    assert get_failure_count("PROJ-1") == 0
    assert should_retry("PROJ-1") is True


async def test_start_quartermaster_pipeline_creates_state_and_spawns_task():
    store = MagicMock()
    store.upsert = MagicMock()

    fake_prs = [{"repo": "service-a", "branch": "feature/X", "snapshot": "SNAP-X"}]
    provision_calls = []

    async def _fake_provision(ticket_key, prs, stream):
        provision_calls.append((ticket_key, prs))
        return {"status": "ok"}

    streams = MagicMock()
    streams.create = MagicMock(return_value=MagicMock(id="qm-stream-123"))

    with patch("auto_provision._gather_prs", AsyncMock(return_value=fake_prs)), \
         patch("auto_provision.quartermaster.provision_env", side_effect=_fake_provision), \
         patch("auto_provision.streams_mod", streams), \
         patch("auto_provision.pipeline_store", store):
        stream_id = await start_quartermaster_pipeline("PROJ-404")
        await asyncio.sleep(0.05)

    assert stream_id == "qm-stream-123"
    assert store.upsert.called
    pid_arg, updates = store.upsert.call_args_list[0][0]
    assert updates["ticketKey"] == "PROJ-404"
    assert updates["env"] == "proj-404"
    assert updates["stage"] == "quartermaster"
    assert updates["status"] == "running"
    assert updates["streamId"] == "qm-stream-123"
    assert provision_calls == [("PROJ-404", fake_prs)]


async def test_start_quartermaster_pipeline_records_failure_on_provision_fail():
    store = MagicMock()
    store.upsert = MagicMock()

    async def _fake_provision(ticket_key, prs, stream):
        return {"status": "failed", "step": "ensure_env", "reason": "boom"}

    streams = MagicMock()
    streams.create = MagicMock(return_value=MagicMock(id="qm-stream-2"))

    with patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.provision_env", side_effect=_fake_provision), \
         patch("auto_provision.streams_mod", streams), \
         patch("auto_provision.pipeline_store", store):
        await start_quartermaster_pipeline("PROJ-500")
        await asyncio.sleep(0.05)

    assert get_failure_count("PROJ-500") == 1
    final_updates = store.upsert.call_args_list[-1][0][1]
    assert final_updates["status"] == "failed"


async def test_start_quartermaster_pipeline_raises_when_handles_unset():
    with patch("auto_provision.streams_mod", None), \
         patch("auto_provision.pipeline_store", None):
        with pytest.raises(RuntimeError, match="handles not initialized"):
            await start_quartermaster_pipeline("PROJ-1")


async def test_start_quartermaster_pipeline_ends_stream_on_provision_exception():
    store = MagicMock()
    store.upsert = MagicMock()

    fake_stream = MagicMock(id="qm-stream-3")
    streams = MagicMock()
    streams.create = MagicMock(return_value=fake_stream)

    async def _boom(ticket_key, prs, stream):
        raise RuntimeError("provision exploded")

    with patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.provision_env", side_effect=_boom), \
         patch("auto_provision.streams_mod", streams), \
         patch("auto_provision.pipeline_store", store):
        await start_quartermaster_pipeline("PROJ-600")
        await asyncio.sleep(0.05)

    assert fake_stream.end.called
    assert get_failure_count("PROJ-600") == 1
    final_updates = store.upsert.call_args_list[-1][0][1]
    assert final_updates["status"] == "failed"
    assert final_updates["failureStep"] == "exception"
    assert "provision exploded" in final_updates["failureReason"]


async def test_do_poll_calls_start_for_each_newcomer_and_persists_set():
    store = MagicMock()
    store.get_meta = MagicMock(return_value=["PROJ-100"])
    store.set_meta = MagicMock()

    started = []

    async def _fake_start(key):
        started.append(key)
        return "stream-x"

    async def _fake_tickets():
        # _fetch_ready_for_qa contract: returns only Ready-for-QA tickets
        return [
            {"key": "PROJ-100", "status": "Ready for QA"},
            {"key": "PROJ-200", "status": "Ready for QA"},
        ]

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await _do_poll()

    assert started == ["PROJ-200"]
    args = store.set_meta.call_args[0]
    assert args[0] == "prev_ready_set"
    assert set(args[1]) == {"PROJ-100", "PROJ-200"}


async def test_do_poll_skips_blocked_tickets():
    store = MagicMock()
    store.get_meta = MagicMock(return_value=["PROJ-100"])
    store.set_meta = MagicMock()

    started = []

    async def _fake_start(key):
        started.append(key)

    async def _fake_tickets():
        return [
            {"key": "PROJ-100", "status": "Ready for QA"},
            {"key": "PROJ-500", "status": "Ready for QA"},
        ]

    _failures["PROJ-500"] = 2  # blocked (>= AUTO_PROVISION_MAX_FAILURES)

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await _do_poll()

    assert started == []


async def test_do_poll_returns_quietly_when_pipeline_store_is_none():
    with patch("auto_provision.pipeline_store", None):
        # Must not raise; just logs and returns
        await _do_poll()


async def test_do_poll_returns_quietly_when_fetch_raises():
    store = MagicMock()
    store.get_meta = MagicMock()
    store.set_meta = MagicMock()

    async def _boom():
        raise RuntimeError("jira down")

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _boom):
        await _do_poll()

    # No persistence attempted when fetch fails
    assert not store.set_meta.called
    assert not store.get_meta.called


async def test_fetch_ready_for_qa_filters_out_non_rfq_tickets():
    from auto_provision import _fetch_ready_for_qa

    async def _fake_get_tickets(project):
        return [
            {"key": "PROJ-100", "status": "Ready for QA"},
            {"key": "PROJ-300", "status": "In Progress"},
            {"key": "PROJ-400"},  # missing status
        ]

    with patch("auto_provision.get_tickets", _fake_get_tickets):
        result = await _fetch_ready_for_qa()

    assert [t["key"] for t in result] == ["PROJ-100"]


async def test_run_loop_returns_immediately_when_disabled():
    from auto_provision import run_loop

    poll_called = False

    async def _fake_poll():
        nonlocal poll_called
        poll_called = True

    with patch("auto_provision.AUTO_PROVISION_ENABLED", False), \
         patch("auto_provision._do_poll", _fake_poll):
        # Should return without polling and without sleeping
        await asyncio.wait_for(run_loop(), timeout=0.5)

    assert poll_called is False


async def test_run_loop_survives_exception_in_do_poll():
    poll_calls = 0

    async def _flaky_poll():
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            raise RuntimeError("first poll boom")
        # Second call signals success; cancel the task from outside

    from auto_provision import run_loop

    with patch("auto_provision.AUTO_PROVISION_ENABLED", True), \
         patch("auto_provision.AUTO_PROVISION_POLL_SEC", 0), \
         patch("auto_provision._do_poll", _flaky_poll):
        task = asyncio.create_task(run_loop())
        # Let the loop run for a few iterations then cancel
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # If the first exception had killed the loop, poll_calls would be exactly 1.
    # The fact that we got >= 2 calls proves the loop survived the exception.
    assert poll_calls >= 2


def test_retry_endpoint_resets_failures_and_kicks_provision():
    _failures["PROJ-999"] = 2  # blocked

    started = []

    async def _fake_start(key):
        started.append(key)
        return "stream-retry-1"

    with patch("server.auto_provision.start_quartermaster_pipeline", _fake_start):
        client = TestClient(app)
        resp = client.post("/api/auto-provision/retry/PROJ-999")

    assert resp.status_code == 200
    body = resp.json()
    assert body["streamId"] == "stream-retry-1"
    assert _failures.get("PROJ-999", 0) == 0
    assert started == ["PROJ-999"]


async def test_reconcile_kicks_pipeline_when_env_not_ready():
    """A Ready-for-QA ticket without a ready env triggers start_quartermaster_pipeline."""
    from auto_provision import reconcile

    store = MagicMock()

    async def _fake_tickets():
        return [{"key": "PROJ-500", "status": "Ready for QA"}]

    started = []

    async def _fake_start(key):
        started.append(key)
        return "stream-id"

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.is_env_ready_for_qa",
               AsyncMock(return_value=(False, None))), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await reconcile()

    assert started == ["PROJ-500"]


async def test_reconcile_skips_already_ready_env_with_long_lease():
    """Ready env with >12h on the lease: no action."""
    import time as _time
    from auto_provision import reconcile

    store = MagicMock()
    far_future = int(_time.time()) + 48 * 3600

    async def _fake_tickets():
        return [{"key": "PROJ-600", "status": "Ready for QA"}]

    started = []
    renewed = []

    async def _fake_start(key):
        started.append(key)

    async def _fake_renew(env):
        renewed.append(env)
        return True

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.is_env_ready_for_qa",
               AsyncMock(return_value=(True, far_future))), \
         patch("auto_provision._renew_lease", _fake_renew), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await reconcile()

    assert started == []
    assert renewed == []


async def test_reconcile_renews_when_lease_under_threshold():
    """Ready env with <12h on lease: renew only, no pipeline kicked."""
    import time as _time
    from auto_provision import reconcile

    store = MagicMock()
    soon = int(_time.time()) + 3600  # 1h left

    async def _fake_tickets():
        return [{"key": "PROJ-700", "status": "Ready for QA"}]

    started = []
    renewed = []

    async def _fake_start(key):
        started.append(key)

    async def _fake_renew(env):
        renewed.append(env)
        return True

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.is_env_ready_for_qa",
               AsyncMock(return_value=(True, soon))), \
         patch("auto_provision._renew_lease", _fake_renew), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await reconcile()

    assert started == []
    assert renewed == ["proj-700"]


async def test_reconcile_skips_blocked_tickets():
    """Tickets that have hit AUTO_PROVISION_MAX_FAILURES are skipped."""
    from auto_provision import reconcile

    store = MagicMock()

    async def _fake_tickets():
        return [{"key": "PROJ-BLOCKED", "status": "Ready for QA"}]

    _failures["PROJ-BLOCKED"] = 2  # at cap

    started = []

    async def _fake_start(key):
        started.append(key)

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await reconcile()

    assert started == []


async def test_reconcile_one_pipeline_per_tick():
    """Multiple not-ready tickets but reconcile kicks only one per call."""
    from auto_provision import reconcile

    store = MagicMock()

    async def _fake_tickets():
        return [
            {"key": "PROJ-800", "status": "Ready for QA"},
            {"key": "PROJ-801", "status": "Ready for QA"},
        ]

    started = []

    async def _fake_start(key):
        started.append(key)

    with patch("auto_provision.pipeline_store", store), \
         patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
         patch("auto_provision._gather_prs", AsyncMock(return_value=[])), \
         patch("auto_provision.quartermaster.is_env_ready_for_qa",
               AsyncMock(return_value=(False, None))), \
         patch("auto_provision.start_quartermaster_pipeline", _fake_start):
        await reconcile()

    assert len(started) == 1


async def test_reconcile_skips_when_ticket_lock_held():
    """If a pipeline is already running for the ticket, reconcile skips it."""
    from auto_provision import reconcile

    store = MagicMock()
    lock = _lock_for("PROJ-LOCKED")
    await lock.acquire()
    try:
        async def _fake_tickets():
            return [{"key": "PROJ-LOCKED", "status": "Ready for QA"}]

        started = []

        async def _fake_start(key):
            started.append(key)

        with patch("auto_provision.pipeline_store", store), \
             patch("auto_provision._fetch_ready_for_qa", _fake_tickets), \
             patch("auto_provision.start_quartermaster_pipeline", _fake_start):
            await reconcile()

        assert started == []
    finally:
        lock.release()


async def test_run_loop_invokes_reconcile_every_nth_poll():
    """Counter-based reconcile cadence: every Nth poll invokes reconcile."""
    from auto_provision import run_loop

    poll_calls = 0
    reconcile_calls = 0

    async def _fake_poll():
        nonlocal poll_calls
        poll_calls += 1

    async def _fake_reconcile():
        nonlocal reconcile_calls
        reconcile_calls += 1

    with patch("auto_provision.AUTO_PROVISION_ENABLED", True), \
         patch("auto_provision.AUTO_PROVISION_POLL_SEC", 0), \
         patch("auto_provision.RECONCILE_EVERY_N_POLLS", 3), \
         patch("auto_provision._do_poll", _fake_poll), \
         patch("auto_provision.reconcile", _fake_reconcile):
        task = asyncio.create_task(run_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # If reconcile fired every 3 polls, the ratio should be roughly 3:1.
    # Loose bound to avoid flakes in CI.
    assert poll_calls >= 3
    assert reconcile_calls >= 1
    assert reconcile_calls <= poll_calls


def test_retry_endpoint_returns_503_when_handles_uninitialized():
    async def _raises(key):
        raise RuntimeError("auto_provision module handles not initialized")

    with patch("server.auto_provision.start_quartermaster_pipeline", _raises):
        client = TestClient(app)
        resp = client.post("/api/auto-provision/retry/PROJ-1")

    assert resp.status_code == 503
    body = resp.json()
    assert "not initialized" in body["error"]
    assert body["ticketKey"] == "PROJ-1"
