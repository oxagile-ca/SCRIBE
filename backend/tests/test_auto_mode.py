import auto_mode


class _FakeStore:
    def __init__(self): self._m = {}
    def get_meta(self, k, default=None): return self._m.get(k, default)
    def set_meta(self, k, v): self._m[k] = v


def test_state_roundtrip():
    auto_mode.configure(_FakeStore(), None)
    assert auto_mode.get_state() == {"enabled": False, "armed": False}
    auto_mode.set_state(enabled=True)
    assert auto_mode.get_state()["enabled"] is True
    assert auto_mode.get_state()["armed"] is False
    auto_mode.set_state(armed=True)
    assert auto_mode.get_state() == {"enabled": True, "armed": True}


def test_eligible_filters_and_sorts():
    tickets = [
        {"key": "INV-3", "statusCategory": "in_qa", "priority": "High"},
        {"key": "INV-1", "statusCategory": "ready_for_qa", "priority": "Low"},
        {"key": "INV-2", "statusCategory": "ready_for_qa", "priority": "Highest"},
    ]
    out = auto_mode.eligible_tickets(tickets)
    assert [t["key"] for t in out] == ["INV-2", "INV-1"]  # only ready_for_qa, priority desc


def test_eligible_skips_processed():
    tickets = [
        {"key": "INV-1", "statusCategory": "ready_for_qa", "priority": "High"},
        {"key": "INV-2", "statusCategory": "ready_for_qa", "priority": "High"},
    ]
    out = auto_mode.eligible_tickets(tickets, skip={"INV-1"})
    assert [t["key"] for t in out] == ["INV-2"]


def test_processed_roundtrip_and_reset_on_enable():
    auto_mode.configure(_FakeStore(), None)
    assert auto_mode.get_processed() == set()
    auto_mode.mark_processed("INV-5")
    assert "INV-5" in auto_mode.get_processed()
    # enabling auto mode (False->True) clears the processed set for a fresh session
    auto_mode.set_state(enabled=True)
    assert auto_mode.get_processed() == set()


# ── single-flight dedup across auto-mode + manual QA runs (INV-675 bug #1) ──
import asyncio
from qa_run_lock import qa_single_flight


def test_process_skips_when_another_qa_run_in_flight(monkeypatch):
    """Auto-mode must not spawn a run while another QA run holds the single-flight
    slot (e.g. a manual /api/qa-run) — that race produced duplicate -001/-002."""
    auto_mode.configure(_FakeStore(), None)
    qa_single_flight._active = None
    assert qa_single_flight.try_acquire("OTHER-9")  # simulate a manual run in flight
    started, processed = [], []

    async def fake_run_and_finalize(key, env, **kw):
        started.append(key)
        yield {"type": "done"}
    monkeypatch.setattr(auto_mode.qa_orchestrator, "run_and_finalize", fake_run_and_finalize)
    monkeypatch.setattr(auto_mode, "mark_processed", lambda k: processed.append(k))

    asyncio.run(auto_mode._process("INV-1", "http://x"))

    assert started == []                            # did not start a second run
    assert processed == []                          # not marked → retried next poll
    assert qa_single_flight.active() == "OTHER-9"   # other run's lock untouched
    qa_single_flight.release("OTHER-9")


def test_process_acquires_and_releases_single_flight(monkeypatch):
    auto_mode.configure(_FakeStore(), None)
    qa_single_flight._active = None
    held = []

    async def fake_run_and_finalize(key, env, **kw):
        held.append(qa_single_flight.active())  # lock held during the run
        yield {"type": "done"}
    monkeypatch.setattr(auto_mode.qa_orchestrator, "run_and_finalize", fake_run_and_finalize)
    monkeypatch.setattr(auto_mode, "mark_processed", lambda k: None)

    asyncio.run(auto_mode._process("INV-1", "http://x"))

    assert held == ["INV-1"]                    # acquired for this ticket
    assert qa_single_flight.active() is None    # released afterwards
