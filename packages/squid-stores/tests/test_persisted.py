"""Contract tests for the reactive persistence bridge."""

from collections.abc import Mapping

import pytest

from squid_reactive import LocalTopicBus, Shared, state, transaction
from squid_stores import MemoryScopedStore, PersistedPool, Slot, json_codec


class Preferences(Shared[str]):
    theme: str = state("dark")


@pytest.fixture
def slot() -> Slot[str, Mapping[str, object]]:
    return Slot("preferences", json_codec())


async def test_load_hydrates_and_reuses_the_canonical_handle(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)

    first = await pool.load("guild")
    second = await pool.load("guild")

    assert first is second
    assert first.theme == "light"
    await pool.close()


async def test_committed_state_persists_but_rolled_back_state_does_not(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)
    preferences = await pool.load("guild")

    with transaction():
        preferences.theme = "light"
    await pool.flush()
    assert await store.get(slot, "guild") == {"theme": "light"}

    with pytest.raises(RuntimeError), transaction():
        preferences.theme = "blue"
        raise RuntimeError("rollback")
    await pool.flush()
    assert await store.get(slot, "guild") == {"theme": "light"}
    await pool.close()


async def test_store_failures_are_reported_without_failing_the_action(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    class BrokenStore(MemoryScopedStore):
        async def put(self, *args: object, **kwargs: object) -> None:
            raise OSError("offline")

    errors: list[BaseException] = []
    pool = PersistedPool(Preferences, LocalTopicBus(), store=BrokenStore(), slot=slot, on_error=errors.append)
    preferences = await pool.load("guild")

    with transaction():
        preferences.theme = "light"
    await pool.flush()

    assert preferences.theme == "light"
    assert [str(error) for error in errors] == ["offline"]
    await pool.close()
