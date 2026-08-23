"""Contract tests for the portable payload-free topic bus and its Postgres bridge."""

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import Any, cast

import anyio
import pytest

from squid_layouts import state
from squid_layouts.runtime import Shared
from squid_layouts.discord.durability import PostgresTopicBridge
from squid_layouts.profiling import MemoryProfiler, OperationKind, TraceOutcome
from squid_layouts.runtime.topics import Address, CellAddress, KindKeyCodec, Topic, TopicBus

BUILD = Topic("build", "1")
"""One address reused by the bus contract tests, whose subject is delivery rather than naming."""
ONE = Topic("one", "1")
TWO = Topic("two", "1")


class Workspace(Shared[int]):
    selected: int | None = state(None)


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

    bus.subscribe(Topic("build", "123"), record, label="build panel")
    for _ in range(100):
        bus.publish(Topic("build", "123"))

    assert bus.snapshot().queued == 1
    await bus.drain()
    assert seen == [Topic("build", "123")]
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

    bus.subscribe(BUILD, block_once)
    bus.publish(BUILD)
    task = asyncio.create_task(bus.drain())
    await first_started.wait()

    bus.publish(BUILD)
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

    bus.subscribe(BUILD, first)
    bus.subscribe(BUILD, second)
    bus.publish(BUILD)

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

    bus.subscribe(ONE, block)
    bus.subscribe(TWO, block)
    bus.publish(ONE, TWO)

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

    bus.subscribe(BUILD, first)
    unsubscribe = bus.subscribe(BUILD, second)
    bus.publish(BUILD)
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

    bus.subscribe(BUILD, fail, label="broken panel")
    bus.subscribe(BUILD, succeed, label="healthy panel")
    bus.publish(BUILD)

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

    bus.subscribe(Topic("build", "42"), refresh, label="mount:instance-42", profile_label="build_projection")
    bus.publish(Topic("build", "42"))
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

    bus.subscribe(BUILD, refresh, profile_label="build_projection")
    with profiler.operation(OperationKind.DISPATCH, name="save"):
        bus.publish(BUILD)
        bus.publish(BUILD)

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

    bus.subscribe(BUILD, fail, profile_label="broken_projection")
    bus.publish(BUILD)

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

    bus.subscribe(BUILD, block)
    bus.publish(BUILD)
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
        bus.publish(TWO)

    async def record(topic: Topic) -> None:
        seen.append(topic)

    bus.subscribe(ONE, publish_other)
    bus.subscribe(TWO, record)
    bus.publish(ONE)

    await bus.drain()

    assert seen == [ONE, TWO]


@pytest.mark.parametrize("concurrency", [0, -1])
def test_concurrency_must_be_positive(concurrency: int) -> None:
    with pytest.raises(ValueError, match="at least one"):
        TopicBus(concurrency=concurrency)


def test_unsubscribe_forgets_an_idle_topic() -> None:
    bus = TopicBus()

    async def callback(topic: Topic) -> None:
        pass

    unsubscribe = bus.subscribe(BUILD, callback)
    unsubscribe()

    assert bus.snapshot().topics == ()


class FakeConnection:
    def __init__(self, server: FakePostgres) -> None:
        self.server = server
        self.terminations: list[Callable[..., None]] = []

    async def add_listener(self, channel: str, callback: Callable[..., None]) -> None:
        self.server.listeners.append((channel, callback))

    def add_termination_listener(self, callback: Callable[..., None]) -> None:
        self.terminations.append(callback)

    async def execute(self, query: str, *arguments: str) -> None:
        assert "pg_notify" in query
        channel, payload = arguments
        self.server.notify(channel, payload)


class _Acquire:
    def __init__(self, server: FakePostgres) -> None:
        self.server = server

    async def __aenter__(self) -> FakeConnection:
        connection = FakeConnection(self.server)
        self.server.connections.append(connection)
        return connection

    async def __aexit__(self, *exception: object) -> None:
        return None


class FakePostgres:
    """One in-memory NOTIFY channel, delivered to every listener including the sender."""

    def __init__(self) -> None:
        self.listeners: list[tuple[str, Callable[..., None]]] = []
        self.connections: list[FakeConnection] = []
        self.sent: list[tuple[str, str]] = []

    def notify(self, channel: str, payload: str) -> None:
        self.sent.append((channel, payload))
        for listening, callback in tuple(self.listeners):
            if listening == channel:
                callback(None, 1, channel, payload)

    def terminate(self) -> None:
        """Drop every connection the way a restarted server would."""
        self.listeners.clear()
        dropped, self.connections = self.connections, []
        for connection in dropped:
            for callback in connection.terminations:
                callback(connection)

    async def execute(self, query: str, *arguments: str) -> None:
        assert "pg_notify" in query
        channel, payload = arguments
        self.notify(channel, payload)

    def acquire(self) -> _Acquire:
        return _Acquire(self)


def _bridge(server: FakePostgres, bus: TopicBus, **options: Any) -> PostgresTopicBridge:
    return PostgresTopicBridge(cast(Any, server), bus, **options)


async def _until(predicate: Callable[[], bool]) -> None:
    with anyio.fail_after(2):
        while not predicate():
            await anyio.sleep(0)


async def _flush(bridge: PostgresTopicBridge) -> None:
    """Wait until every queued notification has left this bridge."""
    with anyio.fail_after(2):
        await bridge._queue.join()


async def test_two_processes_see_each_others_publish_exactly_once() -> None:
    server = FakePostgres()
    here, there = TopicBus(), TopicBus()
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    async def record(into: list[Topic], topic: Topic) -> None:
        into.append(topic)

    here.subscribe(Topic("build", "42"), partial(record, seen_here))
    there.subscribe(Topic("build", "42"), partial(record, seen_there))
    bridge_here = _bridge(server, here)
    bridge_there = _bridge(server, there)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge_here.run)
        tasks.start_soon(bridge_there.run)
        await _until(lambda: len(server.listeners) == 2)
        bridge_here.publish(Topic("build", "42"))
        await _flush(bridge_here)
        tasks.cancel_scope.cancel()

    await here.drain()
    await there.drain()
    assert seen_here == [Topic("build", "42")]
    assert seen_there == [Topic("build", "42")]
    assert bridge_there.snapshot().received == 1
    assert bridge_here.snapshot().ignored == 1


async def test_a_bridge_ignores_its_own_notification() -> None:
    server = FakePostgres()
    bus = TopicBus()
    deliveries = 0

    async def count(topic: Topic) -> None:
        nonlocal deliveries
        deliveries += 1

    bus.subscribe(Topic("build", "42"), count)
    bridge = _bridge(server, bus)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.run)
        await _until(lambda: len(server.listeners) == 1)
        bridge.publish(Topic("build", "42"))
        await _flush(bridge)
        # A relayed self-publish would queue a second delivery behind the first, so the
        # queue depth is what separates "ignored" from "coalesced".
        await bus.drain()
        tasks.cancel_scope.cancel()

    assert deliveries == 1
    assert bus.snapshot().queued == 0
    assert bridge.snapshot().ignored == 1


async def test_publish_in_delivers_to_the_originating_bus_after_notification() -> None:
    server = FakePostgres()
    here, there = TopicBus(), TopicBus()
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    async def record(into: list[Topic], topic: Topic) -> None:
        into.append(topic)

    topic = Topic("build", "42")
    here.subscribe(topic, partial(record, seen_here))
    there.subscribe(topic, partial(record, seen_there))
    bridge_here = _bridge(server, here)
    bridge_there = _bridge(server, there)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge_here.run)
        tasks.start_soon(bridge_there.run)
        await _until(lambda: len(server.listeners) == 2)
        await bridge_here.publish_in(cast(Any, FakeConnection(server)), topic)
        await here.drain()
        await there.drain()
        tasks.cancel_scope.cancel()

    assert seen_here == [topic]
    assert seen_there == [topic]
    assert bridge_here.snapshot().ignored == 0
    assert bridge_here.snapshot().received == 1


async def test_publish_in_rejects_an_unencodable_topic_before_notifying() -> None:
    class NoCodec:
        def encode(self, topic: Topic) -> str | None:
            del topic
            return None

        def decode(self, payload: str) -> Topic | None:
            del payload
            return None

    server = FakePostgres()
    bridge = _bridge(server, TopicBus(), codec=NoCodec())

    with pytest.raises(ValueError, match="cannot be carried"):
        await bridge.publish_in(cast(Any, FakeConnection(server)), Topic("build", "42"))

    assert server.sent == []


async def test_publish_in_requires_a_running_bridge() -> None:
    bridge = _bridge(FakePostgres(), TopicBus())

    with pytest.raises(RuntimeError, match="must be running"):
        await bridge.publish_in(cast(Any, FakeConnection(FakePostgres())), Topic("build", "42"))


async def test_a_cell_address_is_published_locally_only() -> None:
    """A shared cell names a live object, so no wire form for it can exist."""
    server = FakePostgres()
    bus = TopicBus()
    seen: list[Address] = []

    async def record(address: Address) -> None:
        seen.append(address)

    workspace = Workspace(bus, 7)
    address = CellAddress(workspace, "selected")
    bus.subscribe(address, record)
    bridge = _bridge(server, bus)

    bridge.publish(address)
    await bus.drain()

    assert seen == [address]
    assert server.sent == []
    assert bridge.snapshot().local_only == 1


async def test_an_oversized_payload_stays_local() -> None:
    server = FakePostgres()
    bridge = _bridge(server, TopicBus())

    bridge.publish(Topic("build", "x" * 8000))

    assert server.sent == []
    assert bridge.snapshot().local_only == 1


async def test_reconnect_republishes_through_on_resync() -> None:
    server = FakePostgres()
    resyncs = 0

    async def on_resync() -> None:
        nonlocal resyncs
        resyncs += 1

    bridge = _bridge(server, TopicBus(), on_resync=on_resync, reconnect_seconds=0.0)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.run)
        await _until(lambda: resyncs == 1)
        server.terminate()
        await _until(lambda: resyncs == 2)
        tasks.cancel_scope.cancel()

    assert len(server.listeners) == 1


async def test_drain_terminates_with_a_bridge_attached() -> None:
    server = FakePostgres()
    bus = TopicBus()
    seen: list[Topic] = []

    async def record(topic: Topic) -> None:
        seen.append(topic)

    bus.subscribe(Topic("build", "42"), record)
    bridge = _bridge(server, bus)

    bridge.publish(Topic("build", "42"))
    await bus.drain()

    assert seen == [Topic("build", "42")]


async def test_a_malformed_payload_is_counted_rather_than_published() -> None:
    server = FakePostgres()
    bus = TopicBus()
    bridge = _bridge(server, bus)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.run)
        await _until(lambda: len(server.listeners) == 1)
        server.notify(bridge.channel, "no-separator-here")
        tasks.cancel_scope.cancel()

    assert bridge.snapshot().undecodable == 1
    assert bus.snapshot().queued == 0


def test_an_origin_cannot_shadow_the_payload_separator() -> None:
    with pytest.raises(ValueError, match="origin"):
        _bridge(FakePostgres(), TopicBus(), origin="a:b")


# --- Addresses ----------------------------------------------------------------------------


def test_a_topic_is_equal_and_hashes_by_value() -> None:
    """Two publishers agree without sharing a constructor, which is the point of the type."""
    assert Topic("build", "123") == Topic("build", "123")
    assert hash(Topic("build", "123")) == hash(Topic("build", "123"))
    assert Topic("build", "123") != Topic("build", "124")
    assert Topic("build", "123") != Topic("group", "123")
    assert str(Topic("build", "123")) == "build:123"


def test_a_cell_address_separates_two_namespaces_of_one_class() -> None:
    bus = TopicBus()
    first, second = Workspace(bus, 1), Workspace(bus, 2)
    assert CellAddress(first, "selected") == CellAddress(first, "selected")
    assert CellAddress(first, "selected") != CellAddress(second, "selected")
    assert CellAddress(first, "selected") != CellAddress(first, "other")


def test_a_namespace_that_defines_equality_cannot_merge_two_addresses() -> None:
    """Identity, not equality: a host's `__eq__` must not collapse two live namespaces."""

    class Loose(Shared[int]):
        selected: int | None = state(None)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Loose)

        def __hash__(self) -> int:
            return 0

    bus = TopicBus()
    first, second = Loose(bus, 1), Loose(bus, 2)
    assert first == second
    assert CellAddress(first, "selected") != CellAddress(second, "selected")


@pytest.mark.parametrize("topic", [Topic("build", "123"), Topic("build", "a:b"), Topic("b", "x" * 500)])
def test_the_default_codec_round_trips_a_topic(topic: Topic) -> None:
    codec = KindKeyCodec()
    encoded = codec.encode(topic)
    assert encoded is not None
    assert codec.decode(encoded) == topic


def test_the_default_codec_refuses_what_it_cannot_split_back() -> None:
    codec = KindKeyCodec()
    assert codec.encode(Topic("a:b", "123")) is None
    assert codec.encode(Topic("", "123")) is None
    assert codec.encode(Topic("build", "")) is None
    assert codec.decode("build") is None
    assert codec.decode("build:") is None
    assert codec.decode(":123") is None
