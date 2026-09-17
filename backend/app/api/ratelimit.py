"""Per-key sliding-window rate limiting.

Runaway agent loops are expensive, so both the websocket message path and the
upload endpoint are capped. In-process only — good enough for a single Render
instance; swap for Redis if you scale horizontally.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, seconds_until_retry)."""
        now = time.monotonic()
        self._maybe_prune(now)
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, int(self.window - (now - hits[0])) + 1
        hits.append(now)
        return True, 0

    # Keys were never removed, so a limiter keyed by a client-chosen value (a
    # session id, an address) grew by one deque per distinct key for the life
    # of the process — a slow memory leak that a caller could drive on
    # purpose. Idle keys are now swept once a window, at most.
    _last_prune: float = 0.0

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < self.window:
            return
        self._last_prune = now
        stale = [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window]
        for k in stale:
            del self._hits[k]
