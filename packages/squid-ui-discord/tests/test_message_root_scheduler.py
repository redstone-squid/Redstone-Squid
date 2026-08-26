"""Live-update scheduling and topic-following tests."""

import asyncio
import gc
import weakref
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest

from squid_ui import Component
from squid_ui.profiling import MemoryProfiler, OperationKind, TraceLink
from squid_ui.runtime import LocalTopicBus, Topic
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler, PauseUpdates, RenewEphemeral, delivery
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message


class Empty(Component):
    def render(self):
        return []


async def _drain_scheduler(scheduler: MessageRootScheduler) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await asyncio.wait_for(scheduler._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_reactor_refreshes_different_mounts_concurrently() -> None:
    scheduler = MessageRootScheduler(concurrency=2)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()
    message_roots = [
        MessageRoot(Empty(), access=Everyone(), scheduler=scheduler),
        MessageRoot(Empty(), access=Everyone(), scheduler=scheduler),
    ]

    def refresh_for(message_root: MessageRoot):
        async def refresh(*, links: tuple[TraceLink, ...] = ()) -> None:
            started.add(message_root.id)
            if len(started) == 2:
                both_started.set()
            await release.wait()

        return refresh

    for message_root in message_roots:
        message_root.refresh = refresh_for(message_root)  # pyrefly: ignore
        scheduler.schedule(message_root)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        with anyio.fail_after(1):
            await both_started.wait()
        release.set()
        await asyncio.wait_for(scheduler._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_publish_during_refresh_redelivers_without_overlap() -> None:
    scheduler = MessageRootScheduler()
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
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

    message_root.refresh = refresh  # pyrefly: ignore
    scheduler.schedule(message_root)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await first_started.wait()
        scheduler.schedule(message_root)
        release.set()
        await asyncio.wait_for(scheduler._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()

    assert calls == 2
    assert scheduler.snapshot().scheduled == 2
    assert scheduler.snapshot().coalesced == 1
    assert scheduler.snapshot().delivered == 2


async def test_reactor_profile_includes_coalesced_wait_and_links_refresh() -> None:
    monotonic = 100.0

    def clock() -> float:
        return monotonic

    profiler = MemoryProfiler(clock=clock)
    scheduler = MessageRootScheduler(profiler=profiler, monotonic=clock)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
    assert message_root.profiler is profiler
    received_links: tuple[TraceLink, ...] = ()

    async def refresh(*, links: tuple[TraceLink, ...] = ()) -> None:
        nonlocal monotonic, received_links
        received_links = links
        monotonic += 0.5

    message_root.refresh = refresh  # pyrefly: ignore
    with profiler.operation(OperationKind.DISPATCH, name="save"):
        scheduler.schedule(message_root)
        scheduler.schedule(message_root)
    monotonic += 2.0

    await _drain_scheduler(scheduler)

    snapshot = profiler.snapshot()
    producer = next(trace for trace in snapshot.recent if trace.name == "save")
    delivery = snapshot.slow[0]
    queue_wait = next(span for span in delivery.spans if span.name == "queue_wait")
    freshness = next(span for span in delivery.spans if span.name == "freshness")
    assert delivery.operation is OperationKind.SCHEDULER_DELIVERY
    assert delivery.duration == pytest.approx(2.5)
    assert delivery.links[0].trace_id == producer.trace_id
    assert dict((attribute.key, attribute.value) for attribute in queue_wait.attributes)["triggers"] == 2
    assert freshness.duration == pytest.approx(2.5)
    assert {counter.name: counter.value for counter in delivery.counters}["scheduler.coalesced"] == 1
    assert received_links[0].trace_id == delivery.trace_id


async def test_follow_coalesces_topics_and_unsubscribes_on_finish() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
    message_root.refresh = AsyncMock()  # pyrefly: ignore
    scheduler.follow(message_root, Topic("build", "42"), Topic("group", "7"), Topic("index", "recent"))

    bus.publish(Topic("build", "42"), Topic("group", "7"), Topic("index", "recent"))

    assert scheduler._queue.qsize() == 1

    await message_root.finish(disable=False)
    assert bus.snapshot().topics == ()


async def test_follow_rejects_a_message_root_that_already_finished() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
    await message_root.finish(disable=False)

    with pytest.raises(ValueError, match="finished"):
        scheduler.follow(message_root, Topic("build", "42"))


async def test_expiry_sweep_flushes_pause_chrome_once_and_renewal_rearms_it() -> None:
    now = datetime.now(UTC)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=30)
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus, clock=lambda: now)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler, expiry=PauseUpdates(warning=60))
    await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))

    assert message_root in scheduler._watched

    scheduler._sweep_once()
    await _drain_scheduler(scheduler)

    written = interaction.response.edit_message.await_args.kwargs["view"]
    assert "Live updates paused" in str(written.to_components())
    assert scheduler._queue.empty()

    now += timedelta(seconds=1)
    scheduler._sweep_once()
    assert scheduler._queue.empty()

    assert message_root.handle is not None
    message_root.handle.expires_at = now + timedelta(minutes=10)  # pyrefly: ignore[bad-assignment]
    message_root.status = None
    now += timedelta(seconds=1)
    scheduler._sweep_once()
    assert scheduler._queue.empty()

    message_root.handle.expires_at = now + timedelta(seconds=20)  # pyrefly: ignore[bad-assignment]
    now += timedelta(seconds=1)
    scheduler._sweep_once()
    assert scheduler._queue.qsize() == 1


async def test_expiry_watch_does_not_require_a_topic_follow_and_stops_on_finish() -> None:
    scheduler = MessageRootScheduler()
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)

    await message_root.send(delivered_to(fake_message()))

    assert message_root in scheduler._watched
    assert message_root not in scheduler._followed
    await message_root.finish(disable=False)
    assert message_root not in scheduler._watched


async def test_expiry_none_never_schedules_pre_expiry_chrome() -> None:
    now = datetime.now(UTC)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=5)
    scheduler = MessageRootScheduler(clock=lambda: now)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler, expiry=None)
    await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))

    scheduler._sweep_once()

    assert scheduler._queue.empty()


@pytest.mark.parametrize("authority", ["permanent", "unknown_deadline"])
async def test_expiry_sweep_ignores_authority_without_a_temporary_deadline(authority: str) -> None:
    now = datetime.now(UTC)
    scheduler = MessageRootScheduler(clock=lambda: now)
    if authority == "permanent":
        message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
        await message_root.send(delivered_to(fake_message()))
    else:
        interaction = fake_interaction()
        handle = delivery.handle_from(interaction)
        assert handle is not None
        handle.expires_at = None
        message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
        await message_root.send(delivered_to(fake_message(ephemeral=True), handle=handle))

    scheduler._sweep_once()

    assert scheduler._queue.empty()


async def test_expiry_sweep_queues_several_arms_without_waiting_for_discord() -> None:
    now = datetime.now(UTC)
    scheduler = MessageRootScheduler(concurrency=2, clock=lambda: now)
    message_roots = []
    for _ in range(4):
        interaction = fake_interaction()
        interaction.expires_at = now + timedelta(seconds=30)
        message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler, timeout=None)
        await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))
        message_roots.append(message_root)

    scheduler._sweep_once()

    assert scheduler._queue.qsize() == len(message_roots)


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
    scheduler = MessageRootScheduler(clock=lambda: now)
    message_root = MessageRoot(
        Empty(),
        access=Everyone(),
        scheduler=scheduler,
        timeout=timeout,
        expiry=RenewEphemeral(warning=60),
    )
    await message_root.send(delivered_to(fake_message(ephemeral=ephemeral), handle=delivery.handle_from(interaction)))

    scheduler._sweep_once()

    assert scheduler._queue.qsize() == expected


def test_collected_message_root_unsubscribes() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
    scheduler.follow(message_root, Topic("build", "42"))
    reference = weakref.ref(message_root)

    del message_root
    gc.collect()

    assert reference() is None
    assert bus.snapshot().topics == ()


async def test_collected_delivered_message_root_leaves_the_expiry_watch() -> None:
    scheduler = MessageRootScheduler()
    message_root = MessageRoot(Empty(), access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))
    reference = weakref.ref(message_root)

    del message_root
    gc.collect()

    assert reference() is None
    assert not scheduler._watched


def test_follow_requires_its_bus_and_scheduler() -> None:
    message_root = MessageRoot(Empty(), access=Everyone())

    with pytest.raises(RuntimeError, match="topic bus"):
        MessageRootScheduler().follow(message_root, Topic("build", "42"))
    with pytest.raises(ValueError, match="scheduler"):
        MessageRootScheduler(LocalTopicBus()).follow(message_root, Topic("build", "42"))


@pytest.mark.parametrize(
    "options",
    [
        {"concurrency": 0},
        {"sweep_interval": 0},
    ],
)
def test_reactor_rejects_invalid_settings(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MessageRootScheduler(**options)
