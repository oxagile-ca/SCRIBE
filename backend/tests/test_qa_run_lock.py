"""Unit tests for the single-flight guard that serializes QA runs (one at a time)."""
import qa_run_lock


def test_acquire_then_blocked_until_release():
    sf = qa_run_lock.SingleFlight()
    assert sf.active() is None
    assert sf.try_acquire("INV-1") is True
    assert sf.active() == "INV-1"
    # any second run (same or different key) is blocked while one is active
    assert sf.try_acquire("INV-2") is False
    assert sf.try_acquire("INV-1") is False
    sf.release("INV-1")
    assert sf.active() is None
    assert sf.try_acquire("INV-2") is True


def test_release_only_clears_matching_holder():
    sf = qa_run_lock.SingleFlight()
    sf.try_acquire("INV-1")
    sf.release("INV-2")  # stale/foreign release must not free the slot
    assert sf.active() == "INV-1"
    sf.release("INV-1")
    assert sf.active() is None


def test_module_singleton_exists():
    # server.py shares one instance across requests.
    assert isinstance(qa_run_lock.qa_single_flight, qa_run_lock.SingleFlight)
