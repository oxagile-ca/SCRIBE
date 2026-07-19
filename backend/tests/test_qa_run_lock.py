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


def test_stale_lock_auto_expires_so_a_hung_run_cannot_wedge_the_queue():
    # A run that hangs without releasing (INV-681 wedged QA for ~10h) must not block
    # forever: the lock auto-expires past max_age and a new run can proceed.
    t = {"now": 1000.0}
    sf = qa_run_lock.SingleFlight(max_age_s=60, clock=lambda: t["now"])
    assert sf.try_acquire("INV-1") is True
    t["now"] += 61                       # holder now older than max_age
    assert sf.active() is None           # reported free (expired)
    assert sf.try_acquire("INV-2") is True
    assert sf.active() == "INV-2"


def test_lock_held_until_max_age():
    t = {"now": 0.0}
    sf = qa_run_lock.SingleFlight(max_age_s=60, clock=lambda: t["now"])
    sf.try_acquire("INV-1")
    t["now"] = 59
    assert sf.try_acquire("INV-2") is False   # still within max_age → genuinely locked
    assert sf.active() == "INV-1"
    t["now"] = 61
    assert sf.try_acquire("INV-2") is True     # expired → freed


def test_reacquire_resets_the_age_clock():
    t = {"now": 0.0}
    sf = qa_run_lock.SingleFlight(max_age_s=60, clock=lambda: t["now"])
    sf.try_acquire("INV-1")
    t["now"] = 61
    sf.try_acquire("INV-2")               # expires INV-1, acquires INV-2 at t=61
    t["now"] = 100                          # only 39s into INV-2
    assert sf.active() == "INV-2"           # NOT expired — the clock reset on acquire
    assert sf.try_acquire("INV-3") is False


def test_default_max_age_is_past_the_run_cap():
    # 30-min qa_runner total_timeout → lock must outlast a legit run, then expire.
    sf = qa_run_lock.SingleFlight()
    assert sf._max_age_s >= 30 * 60
