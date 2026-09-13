"""Keeps the current Cloudflare clearance and refreshes it through byparr.

Rules:
- Only one refresh runs at a time; concurrent callers wait for it and share the result.
- A caller that saw a challenge passes the clearance it used (`stale`). If someone else
  already replaced it meanwhile, no new byparr solve is started.
- Optionally persisted to a JSON file so a restart doesn't cost a 15-second solve.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .byparr import Clearance, ByparrError

log = logging.getLogger(__name__)


class Solver(Protocol):
    async def solve(self, target_url: str) -> Clearance: ...


@dataclass
class ClearanceStats:
    refreshes: int = 0
    failures: int = 0
    last_error: str | None = None
    last_error_at: float | None = None


class ClearanceManager:
    def __init__(
        self,
        solver: Solver,
        clearance_url: str,
        *,
        max_age: int = 0,
        state_file: Path | None = None,
    ):
        self._solver = solver
        self._clearance_url = clearance_url
        self._max_age = max_age
        self._state_file = state_file
        self._current: Clearance | None = None
        self._lock = asyncio.Lock()
        self._background: asyncio.Task | None = None
        self.stats = ClearanceStats()

    @property
    def current(self) -> Clearance | None:
        return self._current

    def load(self) -> None:
        """Restore clearance saved by a previous run, if any."""
        if not self._state_file or not self._state_file.exists():
            return
        try:
            self._current = Clearance.from_json(json.loads(self._state_file.read_text("utf-8")))
            log.info("loaded clearance from %s (age %.0fs)", self._state_file, self._current.age)
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("ignoring unreadable clearance file %s: %r", self._state_file, e)

    async def get(self) -> Clearance:
        """Current clearance; solves one first if there is none or it is too old."""
        current = self._current
        if current is not None and not self._expired(current):
            return current
        return await self.refresh(stale=current)

    async def refresh(self, stale: Clearance | None) -> Clearance:
        """Replace `stale` with a fresh clearance. Raises ByparrError if byparr fails."""
        async with self._lock:
            # Another request may have refreshed while we were waiting for the lock.
            if self._current is not None and self._current is not stale and not self._expired(self._current):
                return self._current
            try:
                fresh = await self._solver.solve(self._clearance_url)
            except ByparrError as e:
                self.stats.failures += 1
                self.stats.last_error = str(e)
                self.stats.last_error_at = time.time()
                log.error("clearance refresh failed: %s", e)
                raise
            if stale is not None:
                log.info("clearance replaced after %.0fs", stale.age)
            self._current = fresh
            self.stats.refreshes += 1
            self.stats.last_error = None
            self._save(fresh)
            return fresh

    def start_background(self) -> None:
        """Warm up on start and, if max_age is set, refresh ahead of expiry."""
        if self._background is None:
            self._background = asyncio.create_task(self._background_loop(), name="clearance-refresher")

    async def stop_background(self) -> None:
        if self._background is not None:
            self._background.cancel()
            try:
                await self._background
            except asyncio.CancelledError:
                pass
            self._background = None

    async def _background_loop(self) -> None:
        while True:
            try:
                await self.get()
            except ByparrError:
                pass  # already logged; retry on the next tick
            except Exception:  # noqa: BLE001 - background task must not die
                log.exception("unexpected error in clearance refresher")
            await asyncio.sleep(self._next_check_in())

    def _next_check_in(self) -> float:
        current = self._current
        if current is None:
            return 60  # byparr down: try again in a minute
        if self._max_age <= 0:
            return 3600  # on-demand mode: nothing to do proactively
        return max(10.0, self._max_age - current.age)

    def _expired(self, clearance: Clearance) -> bool:
        return self._max_age > 0 and clearance.age >= self._max_age

    def _save(self, clearance: Clearance) -> None:
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(clearance.to_json()), "utf-8")
            os.replace(tmp, self._state_file)
        except OSError as e:
            log.warning("could not save clearance to %s: %r", self._state_file, e)
