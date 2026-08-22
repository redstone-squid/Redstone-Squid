"""Contract tests for the portable payload-free topic bus."""

import asyncio
import logging

import pytest

from squid_layouts.topics import Topic, TopicBus


async def test_burst_coalesces_to_one_queued_topic() -> None:
    bus = TopicBus()
    seen: list[Topic] = []

    async def record(topic: Topic) -> None:
        seen.append(topic)

    bus.subscribe(("build", "123"), record, label="build panel")
    for _ in range(100):
        bus.publish(("build", "123"))

    assert bus.snapshot().queued == 1
    await bus.drain()
    assert seen == [("build", "123")]
    assert bus.snapshot().delivered == 1


async def test_publish_during_delivery_redelivers_after_publish_returns() -> None:
    bus = TopicBus()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    starts = 0

    async def block_once(topic: Topic) -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            first_started.set()
            await release_first.wait()

    bus.subscribe("build", block_once)
    bus.publish("build")
    task = asyncio.create_task(bus.drain())
    await first_started.wait()

    bus.publish("build")
    starts_when_publish_returned = starts
    release_first.set()
    await task

    assert starts_when_publish_returned == 1
    assert starts == 2


async def test_subscribers_are_sequential_and_keep_registration_order() -> None:
    bus = TopicBus()
    first_running = False
    order: list[str] = []

    async def first(topic: Topic) -> None:
        nonlocal first_running
        first_running = True
        order.append("first")
        await asyncio.sleep(0)
        first_running = False

    async def second(topic: Topic) -> None:
        assert not first_running
        order.append("second")

    bus.subscribe("build", first)
    bus.subscribe("build", second)
    bus.publish("build")

    await bus.drain()

    assert order == ["first", "second"]


async def test_different_topics_deliver_concurrently() -> None:
    bus = TopicBus(concurrency=2)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[Topic] = set()

    async def block(topic: Topic) -> None:
        started.add(topic)
        if len(started) == 2:
            both_started.set()
        await release.wait()

    bus.subscribe("one", block)
    bus.subscribe("two", block)
    bus.publish("one", "two")

    task = asyncio.create_task(bus.drain())
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()
    await task


async def test_unsubscribe_before_and_during_drain_is_exact() -> None:
    bus = TopicBus()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    seen: list[str] = []

    async def first(topic: Topic) -> None:
        first_started.set()
        await release_first.wait()

    async def second(topic: Topic) -> None:
        seen.append("second")

    bus.subscribe("build", first)
    unsubscribe = bus.subscribe("build", second)
    bus.publish("build")
    task = asyncio.create_task(bus.drain())
    await first_started.wait()

    unsubscribe()
    unsubscribe()
    release_first.set()
    await task

    assert seen == []
    assert bus.snapshot().topics[0].subscribers == 1


async def test_subscriber_failure_is_logged_and_does_not_stop_siblings(caplog: pytest.LogCaptureFixture) -> None:
    bus = TopicBus()
    seen: list[str] = []

    async def fail(topic: Topic) -> None:
        raise RuntimeError("broken view")

    async def succeed(topic: Topic) -> None:
        seen.append("sibling")

    bus.subscribe("build", fail, label="broken panel")
    bus.subscribe("build", succeed, label="healthy panel")
    bus.publish("build")

    with caplog.at_level(logging.ERROR):
        await bus.drain()

    assert seen == ["sibling"]
    assert bus.snapshot().failed == 1
    assert "broken panel" in caplog.text


async def test_run_cancellation_waits_for_callback_cancellation() -> None:
    bus = TopicBus()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def block(topic: Topic) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    bus.subscribe("build", block)
    bus.publish("build")
    task = asyncio.create_task(bus.run())
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stopped.is_set()
    assert bus.snapshot().in_flight == 0


async def test_drain_processes_reentrant_other_topics() -> None:
    bus = TopicBus()
    seen: list[Topic] = []

    async def publish_other(topic: Topic) -> None:
        seen.append(topic)
        bus.publish("two")

    async def record(topic: Topic) -> None:
        seen.append(topic)

    bus.subscribe("one", publish_other)
    bus.subscribe("two", record)
    bus.publish("one")

    await bus.drain()

    assert seen == ["one", "two"]


@pytest.mark.parametrize("concurrency", [0, -1])
def test_concurrency_must_be_positive(concurrency: int) -> None:
    with pytest.raises(ValueError, match="at least one"):
        TopicBus(concurrency=concurrency)


def test_unsubscribe_forgets_an_idle_topic() -> None:
    bus = TopicBus()

    async def callback(topic: Topic) -> None:
        pass

    unsubscribe = bus.subscribe("build", callback)
    unsubscribe()

    assert bus.snapshot().topics == ()
