"""Single-process sliding-window rate limiter for lightweight API endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict, deque


class InMemoryRateLimiter:
    """Limit requests per socket peer without persisting identity or request data.

    This limiter is intentionally process-local for the single-instance MVP.
    Use a shared store or an API gateway when running multiple workers/instances.
    """

    def __init__(
        self, limit: int, window_seconds: int, *, max_identities: int = 8192
    ) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_identities = max(1, max_identities)
        # Hash keys so usernames and other caller-supplied identifiers are not
        # retained in memory. Bound the map because callers can otherwise create
        # an unbounded number of unique identities.
        self._events: OrderedDict[bytes, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._checks = 0

    async def check(self, identity: str) -> int | None:
        """Record an allowed request; return retry delay when over the limit."""
        now = time.monotonic()
        oldest_allowed = now - self._window_seconds
        identity_key = hashlib.sha256(identity.encode("utf-8", errors="replace")).digest()

        async with self._lock:
            events = self._events.get(identity_key)
            if events is None:
                if len(self._events) >= self._max_identities:
                    self._events.popitem(last=False)
                events = deque()
                self._events[identity_key] = events
            else:
                self._events.move_to_end(identity_key)
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
