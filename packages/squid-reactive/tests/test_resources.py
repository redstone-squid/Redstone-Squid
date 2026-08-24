import asyncio

import anyio
import pytest

from squid_reactive import (
    ActionContext,
    ActionLedger,
    LocalTopicBus,
    Reactive,
    ResourceEventSnapshot,
    Shared,
    Topic,
    action_scope,
    add_action_outcome_sink,
    state,
    transaction,
    watch,
)
from squid_reactive.resources import Pending, Ready, resource


class Source(Reactive):
    key = state("first")

    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1

    @resource
    async def value(self) -> str:
        return self.key.upper()


async def test_resource_tracks_state_and_repends_after_a_source_moves() -> None:
    source = Source()

    assert await source.value.reload() == Ready("FIRST")
    source.key = "second"

    assert source.value.status == Pending(Ready("FIRST"))
    assert await source.value.reload() == Ready("SECOND")


async def test_publish_during_load_repends_the_result() -> None:
    topic = Topic("build", "42")
    bus = LocalTopicBus()
    started = asyncio.Event()
    resume = asyncio.Event()

    class Watched(Reactive):
        @resource
        async def value(self) -> str:
            watch(topic)
            started.set()
            await resume.wait()
            return "loaded"

    owner = Watched()
    loaded = []

    async def load() -> None:
        loaded.append(await owner.value.reload())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(load)
        await started.wait()
        bus.publish(topic)
        resume.set()

    assert loaded == [Ready("loaded")]
    assert owner.value.status == Pending(Ready("loaded"))


async def test_resource_replace_joins_the_transaction() -> None:
    source = Source()
    await source.value.reload()

    with pytest.raises(RuntimeError), transaction():
        source.value.replace("EDITED")
        assert source.value.value == "EDITED"
        raise RuntimeError("abort")

    assert source.value.value == "FIRST"


async def test_shared_resource_publishes_its_cell_address() -> None:
    bus = LocalTopicBus()
    published = []

    class Preferences(Shared[int]):
        @resource
        async def theme(self) -> str:
            return "dark"

    preferences = Preferences(bus, 7)
    unsubscribe = bus.subscribe(preferences.theme.address, published.append)

    await preferences.theme.reload()

    assert published == [preferences.theme.address]
    unsubscribe()


async def test_cancelled_load_attempt_remains_pending_and_retryable() -> None:
    attempts = 0

    class Retryable(Reactive):
        @resource
        async def value(self) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await anyio.sleep_forever()
            return "ready"

    owner = Retryable()

    with anyio.move_on_after(0.01):
        await owner.value

    assert isinstance(owner.value.status, Pending)
    assert await owner.value == "ready"
    assert attempts == 2


async def test_resource_generations_are_causal_without_profiler_retention() -> None:
    source = Source()
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    action = ActionContext.create("refresh")
    try:
        with action_scope(action):
            await source.value.reload()
    finally:
        ledger.close()

    events = [event for event in ledger.events if isinstance(event, ResourceEventSnapshot)]
    assert [event.status for event in events] == ["started", "ready"]
    assert {event.generation_id for event in events} == {events[0].generation_id}
    assert events[0].cause == action.causal_ref()
    assert events[0].root_action_id == str(action.root_action_id)


async def test_an_abandoned_load_does_not_subscribe_the_live_value_to_its_reads() -> None:
    """A superseded loader keeps running, and what it reads on the way out is not a dependency."""
    released = asyncio.Event()

    class Mixer(Reactive):
        abandoned = state(0)
        live = state(0)

        def __init__(self) -> None:
            self.attempt = 0

        def invalidate(self) -> None:
            pass

        @resource
        async def value(self) -> str:
            self.attempt += 1
            if self.attempt == 1:
                # Reads its cell only after the generation that replaces it has finished.
                await released.wait()
                return f"stale-{self.abandoned}"
            return f"live-{self.live}"

    owner = Mixer()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(owner.value._load)
        await asyncio.sleep(0)
        owner.value.invalidate()
        tasks.start_soon(owner.value._load)
        await asyncio.sleep(0)
        released.set()

    assert owner.value.status == Ready("live-0")
    owner.abandoned = 99
    assert owner.value.status == Ready("live-0"), "a cell only the abandoned load read is not a dependency"
    owner.live = 5
    assert owner.value.status == Pending(Ready("live-0")), "a cell the live load read still is one"
