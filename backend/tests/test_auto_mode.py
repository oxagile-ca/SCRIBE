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
