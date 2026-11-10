"""Contract tests for the reactive persistence bridge."""

import asyncio
from collections.abc import Hashable, Mapping
from typing import override

import anyio
import pytest

from squid_reactivity import Reactive, state, transaction
from squid_storage import MemoryScopedStore, PersistentStatePool, Slot, json_codec


class Preferences(Reactive):
    theme: str = state("dark")


def make_preferences(key: str) -> Preferences:
    return Preferences()


@pytest.fixture
def slot() -> Slot[str, Mapping[str, object]]:
    return Slot("preferences", json_codec())


async def test_load_hydrates_and_reuses_the_canonical_handle(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

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
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

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
        @override
        async def put(self, *args: object, **kwargs: object) -> None:
            raise OSError("offline")

    errors: list[BaseException] = []
    pool = PersistentStatePool(make_preferences, store=BrokenStore(), slot=slot, on_error=errors.append)

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
    """Loading and shutdown may occur in different tasks from the worker.

    Anyio requires the task that enters a task group to exit it. A request must be able
    to finish its own cancellation scope while the pool worker remains alive.
    """
    store = MemoryScopedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

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
    pool = PersistentStatePool(make_preferences, store=MemoryScopedStore(), slot=slot)

    with pytest.raises(RuntimeError, match="not running"):
        await pool.load("guild")


async def test_a_closed_pool_refuses_further_loads(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    pool = PersistentStatePool(make_preferences, store=MemoryScopedStore(), slot=slot)

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
    pool = PersistentStatePool(make_preferences, store=MemoryScopedStore(), slot=slot, on_error=errors.append)
    preferences: Preferences | None = None

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")
        await pool.close()

    assert preferences is not None, "the pool must have handed out a handle before closing"

    with transaction():
        preferences.theme = "light"

    assert preferences.theme == "light"
    assert [type(error) for error in errors] == [RuntimeError]
    assert "closed" in str(errors[0])


async def test_a_dropped_generation_stops_persisting_to_the_slot_it_no_longer_owns(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

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
    """Deleting a cached handle preserves its last committed value for future hydration."""
    store = MemoryScopedStore()
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")

        with transaction():
            preferences.theme = "light"
        await pool.delete("guild")

        assert await store.get(slot, "guild") == {"theme": "light"}
        await pool.close()


async def test_dropping_an_absent_scope_returns_none(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistentStatePool(make_preferences, store=MemoryScopedStore(), slot=slot)

    assert await pool.delete("guild") is None
    await pool.close()


async def test_clear_retires_every_scope(slot: Slot[str, Mapping[str, object]]) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        first = await pool.load("one")
        await pool.load("two")

        await pool.clear()

        assert pool.active() == {}
        assert await pool.load("one") is not first
        await pool.close()


async def test_active_snapshots_the_loaded_namespaces(slot: Slot[str, Mapping[str, object]]) -> None:
    pool = PersistentStatePool(make_preferences, store=MemoryScopedStore(), slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        loaded = await pool.load("guild")

        snapshot = pool.active()
        await pool.load("other")

        assert dict(snapshot) == {"guild": loaded}
        assert pool.get_existing("missing") is None
        await pool.close()


async def test_a_closed_pool_stops_persisting(slot: Slot[str, Mapping[str, object]]) -> None:
    store = MemoryScopedStore()
    pool = PersistentStatePool(make_preferences, store=store, slot=slot)

    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        preferences = await pool.load("guild")
        await pool.close()

        with transaction():
            preferences.theme = "light"

        assert await store.get(slot, "guild") is None


async def test_the_factory_can_build_a_model_without_key_metadata(slot: Slot[str, Mapping[str, object]]) -> None:
    class Unscoped(Reactive):
        theme: str = state("dark")

    def make(key: str) -> Unscoped:
        return Unscoped()

    pool = PersistentStatePool(make, store=MemoryScopedStore(), slot=slot)
    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        loaded = await pool.load("guild")
        assert loaded.theme == "dark"
        assert pool.get_existing("guild") is loaded
        await pool.close()


async def test_concurrent_loads_share_one_fully_restored_model(slot: Slot[str, Mapping[str, object]]) -> None:
    entered, resume, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    made: list[Preferences] = []
    loaded: list[Preferences] = []

    class PausedStore(MemoryScopedStore):
        @override
        async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None:
            entered.set()
            await resume.wait()
            return await super().get(slot, scope, touch=touch)

    def make(key: str) -> Preferences:
        model = Preferences()
        made.append(model)
        return model

    store = PausedStore()
    await store.put(slot, "guild", {"theme": "light"})
    pool = PersistentStatePool(make, store=store, slot=slot)

    async def load(*, second: bool = False) -> None:
        if second:
            second_started.set()
        loaded.append(await pool.load("guild"))

    async def load_second() -> None:
        await load(second=True)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as worker:
            await worker.start(pool.run)
            async with anyio.create_task_group() as readers:
                readers.start_soon(load)
                await entered.wait()
                readers.start_soon(load_second)
                await second_started.wait()
                assert pool.get_existing("guild") is None
                assert pool.active() == {}
                resume.set()
            assert len(made) == 1
            assert loaded == [made[0], made[0]]
            assert made[0].theme == "light"
            await pool.close()


async def test_failed_hydration_can_retry_without_retaining_or_saving_the_failed_model(
    slot: Slot[str, Mapping[str, object]],
) -> None:
    class FailingReadStore(MemoryScopedStore):
        fail = True

        @override
        async def get[S: Hashable, V](self, slot: Slot[S, V], scope: S, *, touch: bool = False) -> V | None:
            if self.fail:
                self.fail = False
                raise OSError("read failed")
            return await super().get(slot, scope, touch=touch)

    made: list[Preferences] = []

    def make(key: str) -> Preferences:
        model = Preferences()
        made.append(model)
        return model

    store = FailingReadStore()
    pool = PersistentStatePool(make, store=store, slot=slot)
    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        with pytest.raises(OSError, match="read failed"):
            await pool.load("guild")
        assert pool.active() == {}
        made[0].theme = "unowned"
        await pool.flush()
        assert await store.get(slot, "guild") is None
        loaded = await pool.load("guild")
        assert loaded is made[1]
        assert loaded.theme == "dark"
        await pool.close()


async def test_one_model_cannot_restore_two_storage_scopes(slot: Slot[str, Mapping[str, object]]) -> None:
    model = Preferences()
    store = MemoryScopedStore()
    await store.put(slot, "first", {"theme": "light"})
    await store.put(slot, "second", {"theme": "blue"})
    pool: PersistentStatePool[str, Preferences] = PersistentStatePool(lambda key: model, store=store, slot=slot)
    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        assert await pool.load("first") is model
        with pytest.raises(ValueError, match="different keys"):
            await pool.load("second")
        assert pool.get_existing("second") is None
        assert model.theme == "light"
        model.theme = "changed"
        await pool.flush()
        assert await store.get(slot, "first") == {"theme": "changed"}
        assert await store.get(slot, "second") == {"theme": "blue"}
        await pool.close()


@pytest.mark.parametrize("invalid_result", [False, True])
async def test_failed_construction_leaves_no_cached_model(
    slot: Slot[str, Mapping[str, object]], invalid_result: bool
) -> None:
    def make(key: str) -> Preferences:
        if invalid_result:
            return object()  # type: ignore[bad-return]  # Exercise a factory that violates its annotation.
        raise RuntimeError("factory failed")

    pool = PersistentStatePool(make, store=MemoryScopedStore(), slot=slot)
    async with anyio.create_task_group() as tasks:
        await tasks.start(pool.run)
        with pytest.raises(TypeError if invalid_result else RuntimeError):
            await pool.load("guild")
        assert pool.active() == {}
        await pool.close()
