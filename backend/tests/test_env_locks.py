"""Tests for env locks. The invariant: at most one running pipeline holds
any given env at a time, and stale holders (crashed pipelines, completed
ones) can't permanently block the env."""
import time

import pytest


@pytest.fixture(autouse=True)
def reset_server_state():
    """Each test gets a clean env_locks + pipeline_states dict.

    Import the module fresh-ish — the module-level globals persist between
    tests in the same process, so we clear them by hand."""
    import server
    server.env_locks.clear()
    server.pipeline_states.clear()
    yield
    server.env_locks.clear()
    server.pipeline_states.clear()


def _running_state(env, ticket="PROJ-1", age_sec=0):
    return {
        "env": env,
        "ticketKey": ticket,
        "status": "running",
        "stage": "builder",
        "updated_at": time.time() - age_sec,
    }


def test_acquire_empty_env_succeeds():
    import server
    ok, holder = server.acquire_env_lock("qa-env", "p1")
    assert ok is True
    assert holder == "p1"
    assert server.env_locks["qa-env"] == "p1"


def test_acquire_held_env_fails():
    import server
    server.env_locks["qa-env"] = "p1"
    server.pipeline_states["p1"] = _running_state("qa-env")
    ok, holder = server.acquire_env_lock("qa-env", "p2")
    assert ok is False
    assert holder == "p1"


def test_acquire_is_idempotent_for_same_holder():
    import server
    server.acquire_env_lock("e", "p1")
    server.pipeline_states["p1"] = _running_state("e")
    ok, holder = server.acquire_env_lock("e", "p1")
    assert ok is True
    assert holder == "p1"


def test_release_clears_lock():
    import server
    server.env_locks["e"] = "p1"
    server.release_env_lock("p1")
    assert "e" not in server.env_locks


def test_release_is_idempotent_for_unknown_holder():
    import server
    server.env_locks["e"] = "p1"
    server.release_env_lock("p2")  # not the holder — no change
    assert server.env_locks["e"] == "p1"
    server.release_env_lock("p2")  # again — still no change
    assert server.env_locks["e"] == "p1"


def test_completed_pipeline_lock_is_stale():
    import server
    server.env_locks["e"] = "p1"
    server.pipeline_states["p1"] = {**_running_state("e"), "status": "completed"}
    assert server._env_lock_is_stale("e") is True


def test_failed_pipeline_lock_is_stale():
    import server
    server.env_locks["e"] = "p1"
    server.pipeline_states["p1"] = {**_running_state("e"), "status": "failed"}
    assert server._env_lock_is_stale("e") is True


def test_orphan_lock_is_stale():
    """Holder pipeline_state missing entirely — must be recoverable."""
    import server
    server.env_locks["e"] = "p-vanished"
    assert server._env_lock_is_stale("e") is True


def test_stale_holder_can_be_replaced():
    """Crashed-pipeline scenario: env was locked by p1, p1's state is gone
    or marked failed. p2 must be able to acquire."""
    import server
    server.env_locks["e"] = "p1"
    server.pipeline_states["p1"] = {**_running_state("e"), "status": "failed"}
    ok, holder = server.acquire_env_lock("e", "p2")
    assert ok is True
    assert holder == "p2"
    assert server.env_locks["e"] == "p2"


def test_aged_out_holder_is_stale():
    """A pipeline whose state hasn't ticked in >2h is treated as crashed."""
    import server
    server.env_locks["e"] = "p1"
    server.pipeline_states["p1"] = _running_state("e", age_sec=server.STALE_ENV_LOCK_SECONDS + 60)
    assert server._env_lock_is_stale("e") is True


def test_active_holder_blocks_others_even_if_recently_updated():
    """Sanity: a running, fresh pipeline is NOT stale."""
    import server
    server.env_locks["e"] = "p1"
    server.pipeline_states["p1"] = _running_state("e", age_sec=10)
    assert server._env_lock_is_stale("e") is False
    ok, holder = server.acquire_env_lock("e", "p2")
    assert ok is False
    assert holder == "p1"


def test_empty_env_string_does_not_lock():
    """Pipelines started with empty env (chat, build-only, etc.) shouldn't
    create phantom locks under the empty-string key."""
    import server
    ok, _ = server.acquire_env_lock("", "p1")
    assert ok is True
    assert "" not in server.env_locks


def test_reconcile_from_states_rebuilds_locks():
    """After a backend restart, in-flight pipelines from pipeline-state.json
    should re-claim their envs so a fresh pipeline can't slip in."""
    import server
    server.pipeline_states["p1"] = _running_state("qa-env-1")
    server.pipeline_states["p2"] = {**_running_state("qa-env-2"), "status": "completed"}
    server._reconcile_env_locks_from_states()
    assert server.env_locks == {"qa-env-1": "p1"}  # completed one isn't reclaimed


def test_multi_env_independence():
    import server
    server.acquire_env_lock("e1", "p1")
    server.pipeline_states["p1"] = _running_state("e1")
    ok, _ = server.acquire_env_lock("e2", "p2")
    assert ok is True
    assert set(server.env_locks.keys()) == {"e1", "e2"}
