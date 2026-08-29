"""Expiring suggestion cache tests."""

import anyio

from squid.suggestions.infrastructure.cache import TtlCache


class CountingLoader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def load(self, key: str) -> str:
        self.calls.append(key)
        return f"{key}-{len(self.calls)}"


async def test_a_hit_within_the_ttl_does_not_reload() -> None:
    loader = CountingLoader()
    cache = TtlCache(loader.load, ttl_seconds=60)
    assert await cache.get("a") == "a-1"
    assert await cache.get("a") == "a-1"
    assert loader.calls == ["a"]


async def test_keys_are_cached_independently() -> None:
    loader = CountingLoader()
    cache = TtlCache(loader.load, ttl_seconds=60)
    await cache.get("a")
    await cache.get("b")
    assert loader.calls == ["a", "b"]


async def test_an_expired_entry_reloads() -> None:
    loader = CountingLoader()
    cache = TtlCache(loader.load, ttl_seconds=0)
    assert await cache.get("a") == "a-1"
    assert await cache.get("a") == "a-2"


async def test_invalidate_drops_one_key_or_everything() -> None:
    loader = CountingLoader()
    cache = TtlCache(loader.load, ttl_seconds=60)
    await cache.get("a")
    await cache.get("b")
    cache.invalidate("a")
    await cache.get("a")
    await cache.get("b")
    assert loader.calls == ["a", "b", "a"]
    cache.invalidate()
    await cache.get("b")
    assert loader.calls == ["a", "b", "a", "b"]


async def test_concurrent_misses_on_one_key_issue_a_single_load() -> None:
    """A cold cache under a burst of keystrokes must not stampede the database."""
    loader = CountingLoader()
    cache = TtlCache(loader.load, ttl_seconds=60)
    results: list[str] = []

    async def fetch() -> None:
        results.append(await cache.get("a"))

    async with anyio.create_task_group() as tasks:
        for _ in range(10):
            tasks.start_soon(fetch)

    assert loader.calls == ["a"]
    assert results == ["a-1"] * 10
