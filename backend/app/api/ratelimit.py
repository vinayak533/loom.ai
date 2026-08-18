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
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, int(self.window - (now - hits[0])) + 1
        hits.append(now)
        return True, 0
