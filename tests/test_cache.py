import asyncio

import pytest

from rutracker_proxy.cache import ResponseCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def counting_fetch(value="v", delay=0.0, error=None):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(delay)
        if error:
            raise error
        return f"{value}{calls['n']}"

    return fetch, calls


def always(_):
    return True


async def test_hit_after_miss_and_ttl_expiry():
    clock = FakeClock()
    cache = ResponseCache(ttl=300, max_entries=10, clock=clock)
    fetch, calls = counting_fetch()

    assert await cache.get_or_fetch("k", fetch, always) == ("v1", "miss")
    assert await cache.get_or_fetch("k", fetch, always) == ("v1", "hit")
    clock.now += 301
    assert await cache.get_or_fetch("k", fetch, always) == ("v2", "miss")
    assert calls["n"] == 2
    assert (cache.stats.hits, cache.stats.misses) == (1, 2)


async def test_concurrent_identical_requests_share_one_fetch():
    cache = ResponseCache(ttl=300, max_entries=10)
    fetch, calls = counting_fetch(delay=0.05)
    results = await asyncio.gather(*(cache.get_or_fetch("k", fetch, always) for _ in range(5)))
    assert calls["n"] == 1
    assert [r[0] for r in results] == ["v1"] * 5
    assert sorted(r[1] for r in results) == ["miss", "shared", "shared", "shared", "shared"]


async def test_not_stored_when_store_if_false():
    cache = ResponseCache(ttl=300, max_entries=10)
    fetch, calls = counting_fetch()
    await cache.get_or_fetch("k", fetch, lambda v: False)
    await cache.get_or_fetch("k", fetch, lambda v: False)
    assert calls["n"] == 2
    assert len(cache) == 0


async def test_errors_reach_all_waiters_and_are_not_cached():
    cache = ResponseCache(ttl=300, max_entries=10)
    fetch, calls = counting_fetch(delay=0.02, error=RuntimeError("boom"))
    results = await asyncio.gather(*(cache.get_or_fetch("k", fetch, always) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results)
    assert calls["n"] == 1
    assert len(cache) == 0
    with pytest.raises(RuntimeError):
        await cache.get_or_fetch("k", fetch, always)
    assert calls["n"] == 2


async def test_first_client_disconnect_does_not_cancel_others():
    cache = ResponseCache(ttl=300, max_entries=10)
    fetch, calls = counting_fetch(delay=0.05)
    leader = asyncio.ensure_future(cache.get_or_fetch("k", fetch, always))
    await asyncio.sleep(0.01)
    follower = asyncio.ensure_future(cache.get_or_fetch("k", fetch, always))
    await asyncio.sleep(0.01)
    leader.cancel()
    assert await follower == ("v1", "shared")
    assert calls["n"] == 1
    assert await cache.get_or_fetch("k", fetch, always) == ("v1", "hit")


async def test_lru_eviction():
    cache = ResponseCache(ttl=300, max_entries=2)
    for key in ("a", "b"):
        await cache.get_or_fetch(key, counting_fetch(key)[0], always)
    await cache.get_or_fetch("a", counting_fetch("x")[0], always)  # touch a
    await cache.get_or_fetch("c", counting_fetch("c")[0], always)  # evicts b
    assert (await cache.get_or_fetch("a", counting_fetch("new")[0], always))[1] == "hit"
    assert (await cache.get_or_fetch("b", counting_fetch("new")[0], always))[1] == "miss"


async def test_disabled_cache_still_deduplicates_but_stores_nothing():
    cache = ResponseCache(ttl=0, max_entries=10)
    fetch, calls = counting_fetch(delay=0.02)
    await asyncio.gather(cache.get_or_fetch("k", fetch, always), cache.get_or_fetch("k", fetch, always))
    assert calls["n"] == 1
    assert len(cache) == 0
