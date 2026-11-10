import asyncio

import anyio
import pytest

from squid_reactivity import (
    ActionContext,
    EventLedger,
    LocalTopicBus,
    Reactive,
    ResourceEventSnapshot,
    SubscriptionReconciler,
    action_scope,
    observe_events,
    observe_reads,
    state,
    transaction,
    watch,
)
from squid_reactivity.resources import Pending, PendingMode, Ready, Resource, abandon_superseded_loads, resource


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
    bus = LocalTopicBus()
    topic = bus.topic("build", "42")
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
    published = []

    class Preferences(Reactive):
        @resource
        async def theme(self) -> str:
            return "dark"

    preferences = Preferences()
    address = preferences.theme.address
    assert address is not None
    subscriptions = SubscriptionReconciler(None, published.append)
    subscriptions.stage([address])
    subscriptions.commit()

    await preferences.theme.reload()

    # Reload announces both pending and settled status.
    assert published == [address, address]
    subscriptions.close()


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
    ledger = EventLedger()
    with observe_events(ledger):
        action = ActionContext.create("refresh")
        with action_scope(action):
            await source.value.reload()

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


class _Checkpointed(Reactive):
    """A loader whose first attempt parks at a checkpoint until something releases it."""

    def __init__(self) -> None:
        self.attempts = 0
        self.finished: list[int] = []
        self.released = asyncio.Event()

    @resource
    async def value(self) -> str:
        self.attempts += 1
        attempt = self.attempts
        if attempt == 1:
            await self.released.wait()
        self.finished.append(attempt)
        return f"attempt-{attempt}"


def _generation_statuses(ledger: EventLedger) -> list[str]:
    return [event.status for event in ledger.events if isinstance(event, ResourceEventSnapshot)]


async def test_an_installed_scope_abandons_a_superseded_load() -> None:
    """A host cancellation scope stops an obsolete loader before it returns."""
    owner = _Checkpointed()
    ledger = EventLedger()
    with observe_events(ledger):
        with abandon_superseded_loads(anyio.CancelScope):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(owner.value._load)
                await asyncio.sleep(0)
                owner.value.invalidate()
                tasks.start_soon(owner.value._load)

        assert owner.value.status == Ready("attempt-2")
        assert owner.attempts == 2
        assert owner.finished == [2], "the superseded loader never resumed past its checkpoint"
        assert not owner.released.is_set(), "nothing had to release it, which is the point"
        statuses = _generation_statuses(ledger)
        assert statuses.count("abandoned") == 1
        assert "superseded" not in statuses
        assert statuses[-1] == "ready"


async def test_a_superseded_load_runs_to_completion_without_an_installed_scope() -> None:
    """Without a cancellation scope, the obsolete loader finishes but its result is ignored."""
    owner = _Checkpointed()
    ledger = EventLedger()
    with observe_events(ledger):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(owner.value._load)
            await asyncio.sleep(0)
            owner.value.invalidate()
            tasks.start_soon(owner.value._load)
            await asyncio.sleep(0)
            owner.released.set()

        assert owner.value.status == Ready("attempt-2")
        assert owner.finished == [2, 1], "the abandoned loader ran on and finished after the live one"
        statuses = _generation_statuses(ledger)
        assert statuses.count("superseded") == 1
        assert "abandoned" not in statuses


async def test_replacing_a_resource_abandons_the_load_it_supersedes() -> None:
    """Replacing a value also cancels its obsolete load when a scope is installed."""
    owner = _Checkpointed()

    with abandon_superseded_loads(anyio.CancelScope):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(owner.value._load)
            await asyncio.sleep(0)
            owner.value.replace("authoritative")

    assert owner.value.status == Ready("authoritative")
    assert owner.finished == []


async def test_an_installed_scope_does_not_swallow_a_cancellation_from_outside() -> None:
    """The load scope must let the caller's cancellation reach the caller."""
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

    with abandon_superseded_loads(anyio.CancelScope):
        with anyio.move_on_after(0.01):
            await owner.value

        assert isinstance(owner.value.status, Pending)
        assert await owner.value == "ready"

    assert attempts == 2


async def test_standalone_resource_notifies_readers_without_an_owner_callback() -> None:
    source = Source()

    async def load() -> str:
        return source.key

    value = Resource(source, load, name="standalone", pending_mode=PendingMode.EXPLICIT)
    with observe_reads() as observation:
        assert value.status == Pending()
    seen = []
    subscriptions = SubscriptionReconciler(None, seen.append)
    subscriptions.stage(observation.addresses())
    subscriptions.commit()

    assert await value == "first"
    assert seen == [value.address]
    assert source.invalidations == 0
    assert not observation.current()
    subscriptions.close()
