"""Single-flight guard so server-side QA runs execute one at a time.

Concurrent headless QA runs each spawn a Claude subprocess + Playwright MCP + its
own Chromium; on a single machine two at once starve the backend enough to stall
the SSE stream (the run still finishes, but the live lane shows a false
"Connection lost"). Serializing runs avoids that. FastAPI's event loop is
single-threaded, so the check-and-set here needs no async lock.

The lock AUTO-EXPIRES after ``max_age_s``: a run that hangs without releasing (e.g. a
browser/screenshot MCP call that never returns) must not wedge the whole queue. Once a
holder is older than the max age (set just past qa_runner's 30-min run cap), the next
acquire reclaims the slot instead of 409-ing forever.
"""
import time

# Just past qa_runner's total_timeout_s (30 min): a legitimate run always releases well
# before this; anything still holding past it is stuck and gets reclaimed.
DEFAULT_MAX_AGE_S = 35 * 60


class SingleFlight:
    def __init__(self, max_age_s: float = DEFAULT_MAX_AGE_S, clock=time.monotonic):
        self._active = None
        self._acquired_at = 0.0
        self._max_age_s = max_age_s
        self._clock = clock

    def _maybe_expire(self) -> None:
        """Reclaim the slot if the current holder has exceeded the max age."""
        if self._active is not None and (self._clock() - self._acquired_at) > self._max_age_s:
            self._active = None

    def active(self):
        """The key of the in-flight run, or None (a stale holder reports as free)."""
        self._maybe_expire()
        return self._active

    def try_acquire(self, key) -> bool:
        """Reserve the slot for ``key``. False if a (non-stale) run is already in flight."""
        self._maybe_expire()
        if self._active is not None:
            return False
        self._active = key
        self._acquired_at = self._clock()
        return True

    def release(self, key) -> None:
        """Free the slot — only if ``key`` is the current holder (ignore stale releases)."""
        if self._active == key:
            self._active = None


# One shared instance per backend process.
qa_single_flight = SingleFlight()
