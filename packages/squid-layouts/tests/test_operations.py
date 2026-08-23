"""Operational devtools runtime contracts."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, SessionKey, SessionRegistry
from squid_layouts.discord.durability import PurgeResult
from squid_layouts.discord.operations import (
    ActionDisabled,
    ConfirmationRequired,
    DevToolsAction,
    DevToolsPolicy,
    DevToolsRuntime,
)
from squid_layouts.discord.reactor import ReactorSnapshot
from squid_layouts.discord.sessions import Opened
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.profiling import MemoryProfiler, OperationKind
from squid_layouts.runtime import BusSnapshot


class Panel(sl.Component):
    history: sl.runtime.History = sl.runtime.history(limit=4)
    count: int = sl.state(0)

    def render(self):
        return sl.paragraph(f"count {self.count}")


async def open_panel(registry: SessionRegistry, *, key: SessionKey | None = None) -> Opened:
    result = await registry.open(
        sl.discord.Mount(Panel(), access=Everyone(), timeout=None),
        delivered_to(fake_message()),
        key=key,
    )
    assert isinstance(result, Opened)
    return result


async def test_snapshot_and_mount_inspection_include_sessions_history_and_middleware() -> None:
    registry = SessionRegistry()
    opened = await open_panel(registry, key=SessionKey.global_("devtools"))
    runtime = DevToolsRuntime(sessions=registry)

    snapshot = runtime.snapshot()
    assert snapshot.sessions[0].id == opened.session.id
    assert snapshot.sessions[0].mounts == (opened.session.root.id,)

    inspection = runtime.inspect_mount(opened.session.root.id)
    assert inspection.snapshot.id == opened.session.root.id
    assert inspection.histories[0].name == "history"
    assert inspection.histories[0].undo == ()


async def test_close_session_requires_confirmation_and_finishes_all_mounts() -> None:
    registry = SessionRegistry()
    opened = await open_panel(registry, key=SessionKey.global_("devtools"))
    runtime = DevToolsRuntime(sessions=registry)

    with pytest.raises(ConfirmationRequired):
        await runtime.close_session(opened.session.id)

    result = await runtime.close_session(opened.session.id, confirmed=True)

    assert result.action is DevToolsAction.CLOSE_SESSION
    assert tuple(registry.active()) == ()
    assert opened.session.root.finished


async def test_wait_idle_drains_topics_and_clear_profile_resets_bounded_diagnostics() -> None:
    profiler = MemoryProfiler()
    bus = sl.runtime.TopicBus(profiler=profiler)
    callback = AsyncMock()
    bus.subscribe(sl.runtime.Topic("devtools", "test"), callback, label="test subscriber")
    bus.publish(sl.runtime.Topic("devtools", "test"))
    with profiler.operation(OperationKind.DISPATCH, name="devtools-test"):
        pass
    runtime = DevToolsRuntime(bus=bus, profiler=profiler)

    await runtime.wait_idle()
    assert callback.await_count == 1
    topics = runtime.snapshot().topics
    assert topics is not None
    assert topics.queued == 0

    runtime.clear_profile()
    assert runtime.snapshot().profiler.aggregates == ()


class _IdleQueue:
    def __init__(self) -> None:
        self.queued = 0
        self.in_flight = 0
        self.waits = 0
        self.on_wait: Callable[[], None] | None = None

    def snapshot(self) -> BusSnapshot:
        return BusSnapshot((), queued=self.queued, in_flight=self.in_flight, delivered=0, failed=0)

    async def wait_idle(self) -> None:
        self.waits += 1
        self.queued = 0
        self.in_flight = 0
        if self.on_wait is not None:
            self.on_wait()


class _IdleReactor:
    def __init__(self) -> None:
        self.queued = 0
        self.in_flight = 0
        self.redeliver = 0
        self.waits = 0
        self.on_wait: Callable[[], None] | None = None

    def snapshot(self) -> ReactorSnapshot:
        return ReactorSnapshot(self.queued, self.in_flight, self.redeliver, 0, 0, 0, 0, 0)

    async def wait_idle(self) -> None:
        self.waits += 1
        self.queued = 0
        self.in_flight = 0
        self.redeliver = 0
        if self.on_wait is not None:
            self.on_wait()


async def test_wait_idle_reaches_a_fixed_point_when_bus_schedules_reactor() -> None:
    bus = _IdleQueue()
    reactor = _IdleReactor()
    bus.queued = 1

    def bus_delivery() -> None:
        bus.queued = 0
        reactor.queued = 1

    bus.on_wait = bus_delivery

    runtime = DevToolsRuntime(bus=bus, reactor=reactor)  # type: ignore[arg-type]

    await runtime.wait_idle()

    assert bus.waits == 1
    assert reactor.waits == 1


async def test_wait_idle_reaches_a_fixed_point_when_reactor_publishes_to_bus() -> None:
    bus = _IdleQueue()
    reactor = _IdleReactor()
    reactor.queued = 1

    def reactor_delivery() -> None:
        reactor.on_wait = None
        reactor.queued = 0
        bus.queued = 1

    reactor.on_wait = reactor_delivery

    runtime = DevToolsRuntime(bus=bus, reactor=reactor)  # type: ignore[arg-type]

    await runtime.wait_idle()

    assert bus.waits == 2
    assert reactor.waits == 2


async def test_policy_can_disable_confirmation_required_actions() -> None:
    policy = DevToolsPolicy(
        enabled=frozenset({DevToolsAction.CLEAR_PROFILE}),
        confirmations=frozenset(),
    )
    runtime = DevToolsRuntime(policy=policy)

    with pytest.raises(ActionDisabled):
        await runtime.wait_idle()


async def test_purge_persistence_requires_confirmation_and_returns_store_results() -> None:
    durable = AsyncMock()
    durable.purge.return_value = (PurgeResult(record_key="record-1", deleted=True, reason="deleted"),)
    policy = DevToolsPolicy(
        enabled=frozenset({DevToolsAction.PURGE_PERSISTENCE}),
        confirmations=frozenset({DevToolsAction.PURGE_PERSISTENCE}),
    )
    runtime = DevToolsRuntime(durable=durable, policy=policy)

    with pytest.raises(ConfirmationRequired):
        await runtime.purge_persistence(("record-1",))

    result = await runtime.purge_persistence(("record-1",), confirmed=True)

    assert result == (PurgeResult(record_key="record-1", deleted=True, reason="deleted"),)
    durable.purge.assert_awaited_once_with(("record-1",))
