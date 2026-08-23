"""Contract tests for the local scoped stores."""

from datetime import timedelta
from pathlib import Path

import pytest

from squid_stores import MemoryScopedStore, Slot, SlotVersionError, SQLiteScopedStore, json_codec


@pytest.fixture
def slot() -> Slot[str, dict[str, int] | None]:
    return Slot("preferences", json_codec(), ttl=timedelta(seconds=10))


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_round_trip_expiry_touch_and_reclamation(
    store_kind: str,
    slot: Slot[str, dict[str, int] | None],
    tmp_path: Path,
) -> None:
    now = [100.0]
    if store_kind == "memory":
        store = MemoryScopedStore(clock=lambda: now[0])
    else:
        store = SQLiteScopedStore(tmp_path / "scoped.sqlite3", clock=lambda: now[0])

    assert await store.get(slot, "missing") is None
    await store.put(slot, "guild", {"theme": 1})
    assert await store.get(slot, "guild") == {"theme": 1}

    now[0] = 109.0
    assert await store.get(slot, "guild") == {"theme": 1}
    now[0] = 110.0
    assert await store.get(slot, "guild") is None
    assert await store.purge_expired() == 1
    assert not await store.drop(slot, "guild")


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_touch_and_per_call_ttl(
    store_kind: str,
    slot: Slot[str, dict[str, int] | None],
    tmp_path: Path,
) -> None:
    now = [100.0]
    if store_kind == "memory":
        store = MemoryScopedStore(clock=lambda: now[0])
    else:
        store = SQLiteScopedStore(tmp_path / "scoped.sqlite3", clock=lambda: now[0])

    await store.put(slot, "guild", {"theme": 1}, ttl=timedelta(seconds=5))
    now[0] = 104.0
    assert await store.get(slot, "guild", touch=True) == {"theme": 1}
    now[0] = 108.0
    assert await store.get(slot, "guild") == {"theme": 1}
    now[0] = 109.0
    assert await store.get(slot, "guild") is None


async def test_stored_none_is_a_row_and_newer_versions_are_refused() -> None:
    store = MemoryScopedStore()
    old = Slot("value", json_codec(), version=1)
    current = Slot("value", json_codec(), version=2)
    await store.put(old, "scope", None)
    assert await store.get(current, "scope") is None
    assert await store.drop(current, "scope")

    await store.put(current, "scope", {"version": 2})
    with pytest.raises(SlotVersionError, match="newer than declared"):
        await store.get(old, "scope")
