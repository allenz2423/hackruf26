"""Single-process sliding-window rate limiter for the token endpoint."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class InMemoryRateLimiter:
    """Limit requests per socket peer without persisting identity or request data.

    This limiter is intentionally process-local for the single-instance MVP.
    Use a shared store or an API gateway when running multiple workers/instances.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._checks = 0

    async def check(self, identity: str) -> int | None:
        """Record an allowed request; return retry delay when over the limit."""
        now = time.monotonic()
        oldest_allowed = now - self._window_seconds

        async with self._lock:
            events = self._events.setdefault(identity, deque())
            while events and events[0] <= oldest_allowed:
                events.popleft()

            if len(events) >= self._limit:
                return max(1, int(events[0] + self._window_seconds - now + 0.999))

            events.append(now)
            self._checks += 1
            if self._checks % 128 == 0:
                self._remove_expired(oldest_allowed)
            return None

    def _remove_expired(self, oldest_allowed: float) -> None:
        for identity, events in list(self._events.items()):
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if not events:
                self._events.pop(identity, None)
