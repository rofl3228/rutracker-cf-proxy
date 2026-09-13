"""Short-lived response cache with in-flight de-duplication.

Radarr/Sonarr (through Prowlarr) often send the same search several times in a row.
- Identical requests arriving while one is already running wait for that one ("shared").
- Successful results are kept for `ttl` seconds ("hit"), at most `max_entries` (LRU).
The upstream call runs in its own task, so a client that disconnects does not cancel
the request for the others waiting on it.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    shared: int = 0


class ResponseCache(Generic[T]):
    def __init__(self, ttl: float, max_entries: int, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._in_flight: dict[str, asyncio.Task[T]] = {}
        self.stats = CacheStats()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max_entries > 0

    def __len__(self) -> int:
        return len(self._entries)

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        store_if: Callable[[T], bool],
    ) -> tuple[T, str]:
        """Return (value, source) where source is "hit", "shared" or "miss"."""
        cached = self._lookup(key)
        if cached is not None:
            self.stats.hits += 1
            return cached, "hit"

        task = self._in_flight.get(key)
        if task is not None:
            self.stats.shared += 1
            return await asyncio.shield(task), "shared"

        self.stats.misses += 1
        task = asyncio.ensure_future(fetch())
        self._in_flight[key] = task

        def finished(t: asyncio.Task[T]) -> None:
            self._in_flight.pop(key, None)
            if not t.cancelled() and t.exception() is None and self.enabled and store_if(t.result()):
                self._store(key, t.result())

        task.add_done_callback(finished)
        return await asyncio.shield(task), "miss"

    def _lookup(self, key: str) -> T | None:
        item = self._entries.get(key)
        if item is None:
            return None
        expires, value = item
        if self._clock() >= expires:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def _store(self, key: str, value: T) -> None:
        self._entries[key] = (self._clock() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
