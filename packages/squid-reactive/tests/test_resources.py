import asyncio

import anyio
import pytest
from squid_reactive import LocalTopicBus, Reactive, Shared, Topic, state, transaction, watch
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
