"""Operational devtools runtime contracts."""

from collections.abc import Callable
from unittest.mock import AsyncMock, Mock

import pytest

import squid_discord
import squid_layouts as sl
from squid_discord import Everyone, SessionKey, SessionRegistry
from squid_discord.devtools_runtime import (
    ActionDisabled,
    ConfirmationRequired,
    DevToolsAction,
    DevToolsPolicy,
    DevToolsRuntime,
)
from squid_discord.durability import PurgeResult
from squid_discord.scheduler import MountSchedulerSnapshot
from squid_discord.sessions import Opened
from squid_discord.testing import delivered_to, fake_message
from squid_layouts.profiling import MemoryProfiler, OperationKind
from squid_layouts.runtime import BusSnapshot


class Panel(sl.Component):
    history: sl.runtime.History = sl.runtime.history(limit=4)
    count: int = sl.state(0)

    def render(self):
        return sl.paragraph(f"count {self.count}")


async def open_panel(registry: SessionRegistry, *, key: SessionKey | None = None) -> Opened:
    result = await registry.open(
        squid_discord.Mount(Panel(), access=Everyone(), timeout=None),
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


async def test_session_inspection_reports_membership_and_capacity() -> None:
    registry = SessionRegistry()
    opened = await registry.open(
        squid_discord.Mount(Panel(), access=Everyone(), timeout=None),
        delivered_to(fake_message()),
        key=SessionKey.global_("devtools"),
        actor_id=7,
        capacity=3,
        quota=1,
    )
    assert isinstance(opened, Opened)
    await opened.session.join(8)
    runtime = DevToolsRuntime(sessions=registry)

    inspected = runtime.snapshot().sessions[0]

    assert inspected.members == (7, 8)
    assert inspected.capacity == 3
    assert inspected.quota == 1
    assert inspected.domain == "devtools"
    assert inspected.remaining_capacity == 1
    assert inspected.participants == (7, 8)


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


async def test_wait_idle_observes_sync_topics_and_clear_profile_resets_bounded_diagnostics() -> None:
    profiler = MemoryProfiler()
    bus = sl.runtime.LocalTopicBus()
    callback = Mock()
    bus.subscribe(sl.runtime.Topic("devtools", "test"), callback)
    bus.publish(sl.runtime.Topic("devtools", "test"))
    with profiler.operation(OperationKind.DISPATCH, name="devtools-test"):
        pass
    runtime = DevToolsRuntime(bus=bus, profiler=profiler)

    await runtime.wait_idle()
    callback.assert_called_once_with(sl.runtime.Topic("devtools", "test"))
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


class _IdleScheduler:
    def __init__(self) -> None:
        self.profiler = MemoryProfiler()
        self.queued = 0
        self.in_flight = 0
        self.redeliver = 0
        self.waits = 0
        self.on_wait: Callable[[], None] | None = None

    def snapshot(self) -> MountSchedulerSnapshot:
        return MountSchedulerSnapshot(self.queued, self.in_flight, self.redeliver, 0, 0, 0, 0, 0)

    async def wait_idle(self) -> None:
        self.waits += 1
        self.queued = 0
        self.in_flight = 0
        self.redeliver = 0
        if self.on_wait is not None:
            self.on_wait()


async def test_wait_idle_reaches_a_fixed_point_when_bus_schedules_scheduler() -> None:
    bus = _IdleQueue()
    scheduler = _IdleScheduler()
    bus.queued = 1

    def bus_delivery() -> None:
        bus.queued = 0
        scheduler.queued = 1

    bus.on_wait = bus_delivery

    runtime = DevToolsRuntime(bus=bus, scheduler=scheduler)  # type: ignore[arg-type]

    await runtime.wait_idle()

    assert bus.waits == 1
    assert scheduler.waits == 1


async def test_wait_idle_reaches_a_fixed_point_when_reactor_publishes_to_bus() -> None:
    bus = _IdleQueue()
    scheduler = _IdleScheduler()
    scheduler.queued = 1

    def scheduler_delivery() -> None:
        scheduler.on_wait = None
        scheduler.queued = 0
        bus.queued = 1

    scheduler.on_wait = scheduler_delivery

    runtime = DevToolsRuntime(bus=bus, scheduler=scheduler)  # type: ignore[arg-type]

    await runtime.wait_idle()

    assert bus.waits == 2
    assert scheduler.waits == 2


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
