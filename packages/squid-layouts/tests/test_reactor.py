"""Live-update scheduling and topic-following tests."""

import asyncio
import gc
import weakref
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest

from squid_layouts import Component, TopicBus
from squid_layouts.discord import Everyone, Mount, PauseUpdates, Reactor, RenewEphemeral, delivery
from squid_layouts.discord.testing import delivered_to, fake_interaction, fake_message
from squid_layouts.profiling import MemoryProfiler, OperationKind, TraceLink


class Empty(Component):
    def render(self):
        return []


async def _drain_reactor(reactor: Reactor) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_reactor_refreshes_different_mounts_concurrently() -> None:
    reactor = Reactor(concurrency=2)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()
    mounts = [
        Mount(Empty(), access=Everyone(), scheduler=reactor),
        Mount(Empty(), access=Everyone(), scheduler=reactor),
    ]

    def refresh_for(mount: Mount):
        async def refresh(*, links: tuple[TraceLink, ...] = ()) -> None:
            started.add(mount.id)
            if len(started) == 2:
                both_started.set()
            await release.wait()

        return refresh

    for mount in mounts:
        mount.refresh_now = refresh_for(mount)  # pyrefly: ignore
        reactor.schedule(mount)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        with anyio.fail_after(1):
            await both_started.wait()
        release.set()
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_publish_during_refresh_redelivers_without_overlap() -> None:
    reactor = Reactor()
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)
    first_started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    running = False

    async def refresh(*, links: tuple[TraceLink, ...] = ()) -> None:
        nonlocal calls, running
        assert not running
        running = True
        calls += 1
        if calls == 1:
            first_started.set()
            await release.wait()
        running = False

    mount.refresh_now = refresh  # pyrefly: ignore
    reactor.schedule(mount)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await first_started.wait()
        reactor.schedule(mount)
        release.set()
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()

    assert calls == 2
    assert reactor.snapshot().scheduled == 2
    assert reactor.snapshot().coalesced == 1
    assert reactor.snapshot().delivered == 2


async def test_reactor_profile_includes_coalesced_wait_and_links_refresh() -> None:
    monotonic = 100.0

    def clock() -> float:
        return monotonic

    profiler = MemoryProfiler(clock=clock)
    reactor = Reactor(profiler=profiler, monotonic=clock)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)
    assert mount.profiler is profiler
    received_links: tuple[TraceLink, ...] = ()

    async def refresh(*, links: tuple[TraceLink, ...] = ()) -> None:
        nonlocal monotonic, received_links
        received_links = links
        monotonic += 0.5

    mount.refresh_now = refresh  # pyrefly: ignore
    with profiler.operation(OperationKind.DISPATCH, name="save"):
        reactor.schedule(mount)
        reactor.schedule(mount)
    monotonic += 2.0

    await _drain_reactor(reactor)

    snapshot = profiler.snapshot()
    producer = next(trace for trace in snapshot.recent if trace.name == "save")
    delivery = snapshot.slow[0]
    queue_wait = next(span for span in delivery.spans if span.name == "queue_wait")
    freshness = next(span for span in delivery.spans if span.name == "freshness")
    assert delivery.operation is OperationKind.REACTOR_DELIVERY
    assert delivery.duration == pytest.approx(2.5)
    assert delivery.links[0].trace_id == producer.trace_id
    assert dict((attribute.key, attribute.value) for attribute in queue_wait.attributes)["triggers"] == 2
    assert freshness.duration == pytest.approx(2.5)
    assert {counter.name: counter.value for counter in delivery.counters}["reactor.coalesced"] == 1
    assert received_links[0].trace_id == delivery.trace_id


async def test_follow_coalesces_topics_and_unsubscribes_on_finish() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)
    mount.refresh_now = AsyncMock()  # pyrefly: ignore
    reactor.follow(mount, ("build", "42"), ("group", "7"), ("index", "recent"))

    bus.publish(("build", "42"), ("group", "7"), ("index", "recent"))
    await bus.drain()

    assert reactor._queue.qsize() == 1

    await mount.finish(disable=False)
    assert bus.snapshot().topics == ()


async def test_follow_rejects_a_mount_that_already_finished() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)
    await mount.finish(disable=False)

    with pytest.raises(ValueError, match="finished"):
        reactor.follow(mount, "build")


async def test_expiry_sweep_flushes_pause_chrome_once_and_renewal_rearms_it() -> None:
    now = datetime.now(UTC)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=30)
    bus = TopicBus()
    reactor = Reactor(bus, clock=lambda: now)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor, expiry=PauseUpdates(warning=60))
    await mount.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))

    assert mount in reactor._watched

    reactor._sweep_once()
    await _drain_reactor(reactor)

    written = interaction.response.edit_message.await_args.kwargs["view"]
    assert "Live updates paused" in str(written.to_components())
    assert reactor._queue.empty()

    now += timedelta(seconds=1)
    reactor._sweep_once()
    assert reactor._queue.empty()

    assert mount.handle is not None
    mount.handle.expires_at = now + timedelta(minutes=10)  # pyrefly: ignore[bad-assignment]
    mount.status = None
    now += timedelta(seconds=1)
    reactor._sweep_once()
    assert reactor._queue.empty()

    mount.handle.expires_at = now + timedelta(seconds=20)  # pyrefly: ignore[bad-assignment]
    now += timedelta(seconds=1)
    reactor._sweep_once()
    assert reactor._queue.qsize() == 1


async def test_expiry_watch_does_not_require_a_topic_follow_and_stops_on_finish() -> None:
    reactor = Reactor()
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)

    await mount.send(delivered_to(fake_message()))

    assert mount in reactor._watched
    assert mount not in reactor._followed
    await mount.finish(disable=False)
    assert mount not in reactor._watched


async def test_expiry_none_never_schedules_pre_expiry_chrome() -> None:
    now = datetime.now(UTC)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=5)
    reactor = Reactor(clock=lambda: now)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor, expiry=None)
    await mount.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))

    reactor._sweep_once()

    assert reactor._queue.empty()


@pytest.mark.parametrize(
    ("ephemeral", "timeout", "expected"),
    [(False, None, 0), (True, 10, 0), (True, None, 1)],
)
async def test_renewal_sweep_requires_ephemeral_visibility_and_time_to_renew(
    ephemeral: bool, timeout: float | None, expected: int
) -> None:
    now = datetime.now(UTC)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=30)
    reactor = Reactor(clock=lambda: now)
    mount = Mount(
        Empty(),
        access=Everyone(),
        scheduler=reactor,
        timeout=timeout,
        expiry=RenewEphemeral(warning=60),
    )
    await mount.send(delivered_to(fake_message(ephemeral=ephemeral), handle=delivery.handle_from(interaction)))

    reactor._sweep_once()

    assert reactor._queue.qsize() == expected


def test_collected_mount_unsubscribes() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    mount = Mount(Empty(), access=Everyone(), scheduler=reactor)
    reactor.follow(mount, "build")
    reference = weakref.ref(mount)

    del mount
    gc.collect()

    assert reference() is None
    assert bus.snapshot().topics == ()


def test_follow_requires_its_bus_and_scheduler() -> None:
    mount = Mount(Empty(), access=Everyone())

    with pytest.raises(RuntimeError, match="topic bus"):
        Reactor().follow(mount, "build")
    with pytest.raises(ValueError, match="scheduler"):
        Reactor(TopicBus()).follow(mount, "build")


@pytest.mark.parametrize(
    "options",
    [
        {"concurrency": 0},
        {"sweep_interval": 0},
    ],
)
def test_reactor_rejects_invalid_settings(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Reactor(**options)
