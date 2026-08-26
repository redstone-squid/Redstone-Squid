"""Contract tests for the reactive persistence bridge."""

from collections.abc import Mapping

import anyio
import pytest

from squid_reactivity import LocalTopicBus, SharedState, state, transaction
from squid_storage import MemoryScopedStore, PersistentStatePool, Slot, json_codec


class Preferences(SharedState[str]):
    theme: str = state("dark")


@pytest.fixture
def slot() -> Slot[str, Mapping[str, object]]:
    return Slot("preferences", json_codec())


async def test_load_hydrates_and_reuses_the_canonical_handle(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)

        first = await pool.load("guild")
        second = await pool.load("guild")

        assert first is second
        assert first.theme == "light"
        await pool.close()


async def test_committed_state_persists_but_rolled_back_state_does_not(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
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
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=BrokenStore(), slot=slot, on_error=errors.append)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")

        with transaction():
            preferences.theme = "light"
        await pool.flush()

        assert preferences.theme == "light"
        assert [str(error) for error in errors] == ["offline"]
        await pool.close()


async def test_the_worker_outlives_the_task_that_first_loaded(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    """`load` from a request task, `close` from shutdown -- the ordinary case.

    The worker's task group used to be entered by `load` and exited by `close`, which anyio
    refuses across tasks: the loading task could not even exit its own cancel scope.
    """
    store = MemoryScopedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)

        async with anyio.create_task_group() as loaders:
            loaders.start_soon(pool.load, "guild")

        preferences = await pool.load("guild")
        assert preferences.theme == "light"

        with transaction():
            preferences.theme = "dark"
        await pool.flush()
        assert await store.get(slot, "guild") == {"theme": "dark"}
        await pool.close()


async def test_loading_a_pool_that_is_not_running_is_refused(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)

    with pytest.raises(RuntimeError, match="not running"):
        await pool.load("guild")


async def test_a_closed_pool_refuses_further_loads(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        await pool.load("guild")
        await pool.close()

    with pytest.raises(RuntimeError, match="closed"):
        await pool.load("guild")

    with pytest.raises(RuntimeError, match="closed"):
        await pool.run()


async def test_a_commit_after_close_is_reported_rather_than_dropped(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    errors: list[BaseException] = []
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot, on_error=errors.append)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")
        await pool.close()

    with transaction():
        preferences.theme = "light"

    assert preferences.theme == "light"
    assert [type(error) for error in errors] == [RuntimeError]
    assert "closed" in str(errors[0])


async def test_a_dropped_generation_stops_persisting_to_the_slot_it_no_longer_owns(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        retired = await pool.load("guild")

        assert await pool.delete("guild") is retired

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
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")

        with transaction():
            preferences.theme = "light"
        await pool.delete("guild")

        assert await store.get(slot, "guild") == {"theme": "light"}
        await pool.close()


async def test_dropping_an_absent_scope_returns_none(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)

    assert await pool.delete("guild") is None
    await pool.close()


async def test_clear_retires_every_scope(slot: Slot[str, Mapping[str, object]]) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        first = await pool.load("one")
        await pool.load("two")

        await pool.clear()

        assert pool.active() == {}
        assert await pool.load("one") is not first
        await pool.close()


async def test_active_snapshots_the_loaded_namespaces(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=MemoryScopedStore(), slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        loaded = await pool.load("guild")

        snapshot = pool.active()
        await pool.load("other")

        assert dict(snapshot) == {"guild": loaded}
        assert pool.get_existing("missing") is None
        await pool.close()


async def test_a_closed_pool_stops_persisting(slot: Slot[str, Mapping[str, object]]) -> None:
    """A closed pool refuses the write, so a later commit cannot queue onto a stopped worker."""
    store = MemoryScopedStore()
    pool = PersistentStatePool(Preferences, LocalTopicBus(), store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")
        await pool.close()

        with transaction():
            preferences.theme = "light"

        assert await store.get(slot, "guild") is None


async def test_the_namespace_and_bus_stay_readable(slot: Slot[str, Mapping[str, object]]) -> None:
    bus = LocalTopicBus()
    pool = PersistentStatePool(Preferences, bus, store=MemoryScopedStore(), slot=slot)

    assert pool.namespace is Preferences
    assert pool.bus is bus
