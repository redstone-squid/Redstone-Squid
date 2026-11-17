"""Shared contracts for memory, SQLite, and PostgreSQL scoped values."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import anyio
import pytest

from squid_storage import Slot, SlotVersionError, SQLiteScopedStore, json_codec

from .support import StorageBackend


@pytest.fixture
def slot() -> Slot[str, dict[str, int] | None]:
    return Slot("preferences", json_codec(), ttl=timedelta(seconds=10))


async def test_round_trip_expiry_and_reclamation(
    backend: StorageBackend, slot: Slot[str, dict[str, int] | None]
) -> None:
    store = backend.scoped
    assert await store.get(slot, "missing") is None
    await store.put(slot, "guild", {"theme": 1})
    assert await store.get(slot, "guild") == {"theme": 1}
    await backend.advance(9)
    assert await store.get(slot, "guild") == {"theme": 1}
    await backend.advance(1)
    assert await store.get(slot, "guild") is None
    assert await store.purge() == 1
    assert not await store.delete(slot, "guild")


async def test_touch_and_per_call_ttl(backend: StorageBackend, slot: Slot[str, dict[str, int] | None]) -> None:
    store = backend.scoped
    await store.put(slot, "guild", {"theme": 1}, ttl=timedelta(seconds=5))
    await backend.advance(4)
    assert await store.get(slot, "guild", touch=True) == {"theme": 1}
    await backend.advance(4)
    assert await store.get(slot, "guild") == {"theme": 1}
    await backend.advance(1)
    assert await store.get(slot, "guild", touch=True) is None


async def test_stored_none_is_a_row_and_newer_versions_are_refused(backend: StorageBackend) -> None:
    store = backend.scoped
    old = Slot[str, object]("value", json_codec(), version=1)
    current = Slot[str, object]("value", json_codec(), version=2)
    await store.put(old, "scope", None)
    assert await store.get(current, "scope") is None
    assert await store.delete(current, "scope")
    await store.put(current, "scope", {"version": 2})
    with pytest.raises(SlotVersionError, match="newer than declared"):
        await store.get(old, "scope")


async def test_scopes_and_slot_names_are_independent(backend: StorageBackend) -> None:
    store = backend.scoped
    first = Slot[str, list[int]]("first", json_codec())
    second = Slot[str, list[int]]("second", json_codec())
    value = [1]
    await store.put(first, "a", value)
    value.append(2)
    await store.put(first, "b", [3])
    await store.put(second, "a", [4])
    read = await store.get(first, "a")
    assert read is not None
    assert read == [1]
    read.append(9)
    assert await store.get(first, "a", touch=True) == [1]
    assert await store.get(first, "b") == [3]
    assert await store.get(second, "a") == [4]
    await backend.advance(100)
    assert await store.get(first, "a") == [1]
    assert await store.purge() == 0


async def test_decoder_migrates_using_the_stored_version(backend: StorageBackend) -> None:
    old = Slot[str, dict[str, int]]("value", json_codec(), version=1)
    migrated = Slot[str, dict[str, int]](
        "value", json_codec(lambda raw, version: {"current": raw["old"] + version}), version=2
    )
    await backend.scoped.put(old, "scope", {"old": 7})
    assert await backend.scoped.get(migrated, "scope") == {"current": 8}


async def test_delete_reports_expired_rows(backend: StorageBackend) -> None:
    slot = Slot[str, int]("value", json_codec(), ttl=timedelta(seconds=10))
    await backend.scoped.put(slot, "scope", 1)
    await backend.advance(10.1)
    assert await backend.scoped.get(slot, "scope") is None
    assert await backend.scoped.delete(slot, "scope")
    assert not await backend.scoped.delete(slot, "scope")


async def test_sqlite_touch_serializes_replacement(tmp_path: Path) -> None:
    path = tmp_path / "scoped.sqlite3"
    now = 100.0
    replace_during_touch = False
    replacement_blocked = False
    touch_reads = 0

    def clock() -> float:
        nonlocal replacement_blocked, touch_reads
        if replace_during_touch:
            touch_reads += 1
        if touch_reads == 2:
            # Simulate another writer between the touch's read and expiry update.
            with sqlite3.connect(path, timeout=0) as connection:
                try:
                    connection.execute(
                        "UPDATE squid_scoped_values SET payload = '2', ttl_seconds = 100, expires_at = 200"
                    )
                except sqlite3.OperationalError as error:
                    assert "locked" in str(error)
                    replacement_blocked = True
        return now

    store = SQLiteScopedStore(path, clock=clock)
    slot = Slot[str, int]("value", json_codec(), ttl=timedelta(seconds=10))
    await store.put(slot, "scope", 1)
    replace_during_touch = True
    assert await store.get(slot, "scope", touch=True) == 1
    assert replacement_blocked
    replace_during_touch = False
    touch_reads = 0
    await store.put(slot, "scope", 2, ttl=timedelta(seconds=100))
    now = 111.0
    assert await store.get(slot, "scope") == 2


async def test_sqlite_instances_initialize_one_file_concurrently(tmp_path: Path) -> None:
    stores = [SQLiteScopedStore(tmp_path / "shared.sqlite3") for _ in range(8)]
    slot = Slot[str, int]("value", json_codec())
    async with anyio.create_task_group() as tasks:
        for index, store in enumerate(stores):
            tasks.start_soon(store.put, slot, str(index), index)
    for index in range(len(stores)):
        assert await stores[0].get(slot, str(index)) == index
