"""Contract tests for the portable payload-free topic bus."""

import asyncio
import logging

import pytest

from squid_layouts.profiling import MemoryProfiler, OperationKind, TraceOutcome
from squid_layouts.topics import Topic, TopicBus


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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


async def test_delivery_profile_includes_queue_wait_and_stable_subscriber_spans() -> None:
    clock = Clock()
    profiler = MemoryProfiler(clock=clock)
    bus = TopicBus(profiler=profiler, clock=clock)

    async def refresh(topic: Topic) -> None:
        clock.advance(0.5)

    bus.subscribe(("build", 42), refresh, label="mount:instance-42", profile_label="build_projection")
    bus.publish(("build", 42))
    clock.advance(2.0)

    await bus.drain()

    trace = profiler.snapshot().slow[0]
    assert trace.operation is OperationKind.TOPIC_DELIVERY
    assert trace.name == "topic"
    assert trace.duration == pytest.approx(2.5)
    spans = {span.name: span for span in trace.spans}
    assert spans["queue_wait"].duration == pytest.approx(2.0)
    assert spans["subscriber:build_projection"].duration == pytest.approx(0.5)
    assert all("instance-42" not in span.name for span in trace.spans)


async def test_coalesced_delivery_retains_producer_link_and_trigger_count() -> None:
    profiler = MemoryProfiler()
    bus = TopicBus(profiler=profiler)

    async def refresh(topic: Topic) -> None:
        pass

    bus.subscribe("build", refresh, profile_label="build_projection")
    with profiler.operation(OperationKind.DISPATCH, name="save"):
        bus.publish("build")
        bus.publish("build")

    await bus.drain()

    traces = profiler.snapshot().recent
    producer = next(trace for trace in traces if trace.name == "save")
    delivery = next(trace for trace in traces if trace.operation is OperationKind.TOPIC_DELIVERY)
    queue_wait = next(span for span in delivery.spans if span.name == "queue_wait")
    freshness = next(span for span in delivery.spans if span.name == "freshness")
    assert delivery.links[0].trace_id == producer.trace_id
    assert dict((attribute.key, attribute.value) for attribute in queue_wait.attributes)["triggers"] == 2
    assert freshness.duration >= 0
    assert {counter.name: counter.value for counter in delivery.counters}["topic.coalesced"] == 1


async def test_caught_subscriber_failure_marks_delivery_trace_failed() -> None:
    profiler = MemoryProfiler()
    bus = TopicBus(profiler=profiler)

    async def fail(topic: Topic) -> None:
        raise RuntimeError("broken view")

    bus.subscribe("build", fail, profile_label="broken_projection")
    bus.publish("build")

    await bus.drain()

    trace = profiler.snapshot().failed[0]
    subscriber = next(span for span in trace.spans if span.name == "subscriber:broken_projection")
    assert trace.result.outcome is TraceOutcome.FAILED
    assert trace.result.detail == "subscriber_failed"
    assert subscriber.outcome is TraceOutcome.FAILED


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
