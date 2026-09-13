import asyncio
import json
import time

import pytest

from rutracker_proxy.byparr import ByparrError, Clearance
from rutracker_proxy.clearance import ClearanceManager

URL = "https://rutracker.org/forum/login.php"


class FakeSolver:
    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.calls = 0
        self.delay = delay
        self.fail = fail

    async def solve(self, target_url: str) -> Clearance:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ByparrError("byparr down")
        return Clearance(user_agent="UA", cookies={"cf_clearance": f"v{self.calls}"})


async def test_get_solves_once_and_caches():
    solver = FakeSolver()
    manager = ClearanceManager(solver, URL)
    first = await manager.get()
    second = await manager.get()
    assert first is second
    assert solver.calls == 1


async def test_concurrent_refreshes_share_one_solve():
    solver = FakeSolver(delay=0.05)
    manager = ClearanceManager(solver, URL)
    stale = await manager.get()

    results = await asyncio.gather(*(manager.refresh(stale=stale) for _ in range(10)))

    assert solver.calls == 2  # initial + exactly one refresh
    assert all(r is results[0] for r in results)
    assert results[0].cookies["cf_clearance"] == "v2"


async def test_refresh_with_already_replaced_stale_does_not_solve():
    solver = FakeSolver()
    manager = ClearanceManager(solver, URL)
    old = await manager.get()
    new = await manager.refresh(stale=old)
    again = await manager.refresh(stale=old)  # a late caller that also saw the old one fail
    assert again is new
    assert solver.calls == 2


async def test_max_age_triggers_refresh():
    solver = FakeSolver()
    manager = ClearanceManager(solver, URL, max_age=60)
    manager._current = Clearance(user_agent="UA", cookies={}, obtained_at=time.time() - 61)
    fresh = await manager.get()
    assert solver.calls == 1
    assert fresh.cookies == {"cf_clearance": "v1"}


async def test_failure_is_recorded_and_raised():
    manager = ClearanceManager(FakeSolver(fail=True), URL)
    with pytest.raises(ByparrError):
        await manager.get()
    assert manager.stats.failures == 1
    assert manager.stats.last_error == "byparr down"
    assert manager.current is None


async def test_persisted_across_restarts(tmp_path):
    state = tmp_path / "sub" / "clearance.json"
    first = ClearanceManager(FakeSolver(), URL, state_file=state)
    saved = await first.get()
    assert json.loads(state.read_text())["cookies"] == {"cf_clearance": "v1"}

    solver = FakeSolver()
    second = ClearanceManager(solver, URL, state_file=state)
    second.load()
    restored = await second.get()
    assert solver.calls == 0
    assert restored.cookies == saved.cookies
    assert restored.obtained_at == saved.obtained_at


async def test_corrupt_state_file_is_ignored(tmp_path):
    state = tmp_path / "clearance.json"
    state.write_text("{not json")
    manager = ClearanceManager(FakeSolver(), URL, state_file=state)
    manager.load()
    assert manager.current is None


async def test_background_warms_up():
    solver = FakeSolver()
    manager = ClearanceManager(solver, URL)
    manager.start_background()
    for _ in range(50):
        if manager.current is not None:
            break
        await asyncio.sleep(0.01)
    await manager.stop_background()
    assert manager.current is not None
    assert solver.calls == 1
