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


async def test_a_dropped_generation_stops_persisting_to_the_slot_it_no_longer_owns(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)
    retired = await pool.load("guild")

    assert await pool.drop("guild") is retired

    fresh = await pool.load("guild")
    assert fresh is not retired

    with transaction():
        fresh.theme = "light"
    await pool.flush()
    assert await store.get(slot, "guild") == {"theme": "light"}

    # The retired handle is still live and still reactive -- it simply owns no slot any more,
    # so it cannot overwrite the generation that replaced it.
    with transaction():
        retired.theme = "resurrected"
    await pool.flush()
    assert retired.theme == "resurrected"
    assert await store.get(slot, "guild") == {"theme": "light"}
    await pool.close()


async def test_drop_writes_out_what_the_retired_handle_already_committed(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    """A drop retires a lifetime; it does not undo a committed action."""
    store = MemoryScopedStore()
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)
    preferences = await pool.load("guild")

    with transaction():
        preferences.theme = "light"
    await pool.drop("guild")

    assert await store.get(slot, "guild") == {"theme": "light"}
    await pool.close()


async def test_dropping_an_absent_scope_returns_none(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistedPool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)

    assert await pool.drop("guild") is None
    await pool.close()


async def test_clear_retires_every_scope(slot: Slot[str, Mapping[str, object]]) -> None:
    store = MemoryScopedStore()
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)
    first = await pool.load("one")
    await pool.load("two")

    await pool.clear()

    assert pool.active() == {}
    assert await pool.load("one") is not first
    await pool.close()


async def test_active_snapshots_the_loaded_namespaces(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistedPool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)
    loaded = await pool.load("guild")

    snapshot = pool.active()
    await pool.load("other")

    assert dict(snapshot) == {"guild": loaded}
    assert pool.get_existing("missing") is None
    await pool.close()


async def test_a_closed_pool_stops_persisting(slot: Slot[str, Mapping[str, object]]) -> None:
    """Closing detaches listeners, so a later commit cannot queue onto a stopped worker."""
    store = MemoryScopedStore()
    pool = PersistedPool(Preferences, LocalTopicBus(), store=store, slot=slot)
    preferences = await pool.load("guild")
    await pool.close()

    with transaction():
        preferences.theme = "light"

    assert await store.get(slot, "guild") is None


async def test_the_namespace_and_bus_stay_readable(slot: Slot[str, Mapping[str, object]]) -> None:
    bus = LocalTopicBus()
    pool = PersistedPool(Preferences, bus, store=MemoryScopedStore(), slot=slot)

    assert pool.namespace is Preferences
    assert pool.bus is bus
