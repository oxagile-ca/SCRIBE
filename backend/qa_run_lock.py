"""Single-flight guard so server-side QA runs execute one at a time.

Concurrent headless QA runs each spawn a Claude subprocess + Playwright MCP + its
own Chromium; on a single machine two at once starve the backend enough to stall
the SSE stream (the run still finishes, but the live lane shows a false
"Connection lost"). Serializing runs avoids that. FastAPI's event loop is
single-threaded, so the check-and-set here needs no async lock.
"""


class SingleFlight:
    def __init__(self):
        self._active = None

    def active(self):
        """The key of the in-flight run, or None."""
        return self._active

    def try_acquire(self, key) -> bool:
        """Reserve the slot for ``key``. False if a run is already in flight."""
        if self._active is not None:
            return False
        self._active = key
        return True

    def release(self, key) -> None:
        """Free the slot — only if ``key`` is the current holder (ignore stale releases)."""
        if self._active == key:
            self._active = None


# One shared instance per backend process.
qa_single_flight = SingleFlight()
