"""Contract tests for the Postgres topic bridge and portable addresses."""

from collections.abc import Callable
from functools import partial
from typing import Any, cast

import anyio
import pytest

from squid_ui import state
from squid_ui.runtime import SharedState
from squid_ui.runtime.topics import Address, CellAddress, KindKeyCodec, LocalTopicBus, Topic, TopicBus
from squid_ui_discord.durability import PostgresTopicBridge


class Workspace(SharedState[int]):
    selected: int | None = state(None)


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
    here, there = LocalTopicBus(), LocalTopicBus()
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    def record(into: list[Topic], topic: Topic) -> None:
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

    assert seen_here == [Topic("build", "42")]
    assert seen_there == [Topic("build", "42")]
    assert bridge_there.snapshot().received == 1
    assert bridge_here.snapshot().ignored == 1


async def test_a_bridge_ignores_its_own_notification() -> None:
    server = FakePostgres()
    bus = LocalTopicBus()
    deliveries = 0

    def count(topic: Topic) -> None:
        nonlocal deliveries
        deliveries += 1

    bus.subscribe(Topic("build", "42"), count)
    bridge = _bridge(server, bus)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.run)
        await _until(lambda: len(server.listeners) == 1)
        bridge.publish(Topic("build", "42"))
        await _flush(bridge)
        tasks.cancel_scope.cancel()

    assert deliveries == 1
    assert bus.snapshot().queued == 0
    assert bridge.snapshot().ignored == 1


async def test_publish_in_delivers_to_the_originating_bus_after_notification() -> None:
    server = FakePostgres()
    here, there = LocalTopicBus(), LocalTopicBus()
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    def record(into: list[Topic], topic: Topic) -> None:
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
    bridge = _bridge(server, LocalTopicBus(), codec=NoCodec())

    with pytest.raises(ValueError, match="cannot be carried"):
        await bridge.publish_in(cast(Any, FakeConnection(server)), Topic("build", "42"))

    assert server.sent == []


async def test_publish_in_requires_a_running_bridge() -> None:
    bridge = _bridge(FakePostgres(), LocalTopicBus())

    with pytest.raises(RuntimeError, match="must be running"):
        await bridge.publish_in(cast(Any, FakeConnection(FakePostgres())), Topic("build", "42"))


async def test_a_cell_address_is_published_locally_only() -> None:
    """A shared cell names a live object, so no wire form for it can exist."""
    server = FakePostgres()
    bus = LocalTopicBus()
    seen: list[Address] = []

    def record(address: Address) -> None:
        seen.append(address)

    workspace = Workspace(bus, 7)
    address = CellAddress(workspace, "selected")
    bus.subscribe(address, record)
    bridge = _bridge(server, bus)

    bridge.publish(address)

    assert seen == [address]
    assert server.sent == []
    assert bridge.snapshot().local_only == 1


async def test_an_oversized_payload_stays_local() -> None:
    server = FakePostgres()
    bridge = _bridge(server, LocalTopicBus())

    bridge.publish(Topic("build", "x" * 8000))

    assert server.sent == []
    assert bridge.snapshot().local_only == 1


async def test_reconnect_republishes_through_on_resync() -> None:
    server = FakePostgres()
    resyncs = 0

    async def on_resync() -> None:
        nonlocal resyncs
        resyncs += 1

    bridge = _bridge(server, LocalTopicBus(), on_resync=on_resync, reconnect_seconds=0.0)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.run)
        await _until(lambda: resyncs == 1)
        server.terminate()
        await _until(lambda: resyncs == 2)
        tasks.cancel_scope.cancel()

    assert len(server.listeners) == 1


async def test_bridge_publish_delivers_locally_before_returning() -> None:
    server = FakePostgres()
    bus = LocalTopicBus()
    seen: list[Topic] = []

    def record(topic: Topic) -> None:
        seen.append(topic)

    bus.subscribe(Topic("build", "42"), record)
    bridge = _bridge(server, bus)

    bridge.publish(Topic("build", "42"))

    assert seen == [Topic("build", "42")]


async def test_a_malformed_payload_is_counted_rather_than_published() -> None:
    server = FakePostgres()
    bus = LocalTopicBus()
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
        _bridge(FakePostgres(), LocalTopicBus(), origin="a:b")


# --- Addresses ----------------------------------------------------------------------------


def test_a_topic_is_equal_and_hashes_by_value() -> None:
    """Two publishers agree without sharing a constructor, which is the point of the type."""
    assert Topic("build", "123") == Topic("build", "123")
    assert hash(Topic("build", "123")) == hash(Topic("build", "123"))
    assert Topic("build", "123") != Topic("build", "124")
    assert Topic("build", "123") != Topic("group", "123")
    assert str(Topic("build", "123")) == "build:123"


def test_a_cell_address_separates_two_namespaces_of_one_class() -> None:
    bus = LocalTopicBus()
    first, second = Workspace(bus, 1), Workspace(bus, 2)
    assert CellAddress(first, "selected") == CellAddress(first, "selected")
    assert CellAddress(first, "selected") != CellAddress(second, "selected")
    assert CellAddress(first, "selected") != CellAddress(first, "other")


def test_a_namespace_that_defines_equality_cannot_merge_two_addresses() -> None:
    """Identity, not equality: a host's `__eq__` must not collapse two live namespaces."""

    class Loose(SharedState[int]):
        selected: int | None = state(None)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Loose)

        def __hash__(self) -> int:
            return 0

    bus = LocalTopicBus()
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
