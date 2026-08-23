"""End-to-end durable session coordination without Discord network I/O."""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import anyio
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Opened, SessionKey, SessionPolicy, SessionRegistry, Unprotected
from squid_layouts.discord.delivery import DeliveryReceipt
from squid_layouts.discord.durability import (
    ComponentRegistry,
    DurableSession,
    DurableSessionCodec,
    DurableSessionRuntime,
    MemorySnapshotStore,
    Missing,
    MountLocator,
    NotDurable,
    Promoted,
    Reconnected,
    RecoveredBinding,
    SnapshotError,
    Unreachable,
)
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.primitives import Text
from squid_layouts.profiling import PresentationOutcome


class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return Text(f"count {self.count}")


class HiddenDraft(sl.Component):
    advanced: bool = sl.state(False)

    def render(self):
        return Text("Draft")


@dataclass(slots=True)
class FakeFrontend:
    reject_next: bool = False
    missing_ids: frozenset[str] = frozenset()
    unreachable_ids: frozenset[str] = frozenset()

    async def promote(self, mount: sl.discord.Mount, receipt: DeliveryReceipt):
        if self.reject_next:
            self.reject_next = False
            return NotDurable("test destination is temporary")
        if receipt.message is None or receipt.handle is None or receipt.ephemeral:
            return NotDurable("message has no durable binding")
        await mount.adopt_handle(receipt.handle)
        return Promoted(
            MountLocator(
                "fake",
                {"channel_id": receipt.message.channel.id, "message_id": receipt.message.id},
            ),
            receipt.handle,
        )

    async def reconnect(self, bindings: Sequence[RecoveredBinding]):
        missing = tuple(binding.record_mount_id for binding in bindings if binding.record_mount_id in self.missing_ids)
        if missing:
            return Missing(missing, tuple("test message is gone" for _ in missing))
        unreachable = tuple(
            binding.record_mount_id for binding in bindings if binding.record_mount_id in self.unreachable_ids
        )
        if unreachable:
            return Unreachable(unreachable, tuple("test frontend is unavailable" for _ in unreachable))
        for binding in bindings:
            message_id = binding.locator.values["message_id"]
            assert isinstance(message_id, int)
            await binding.mount.send(delivered_to(fake_message(message_id=message_id)))
        return Reconnected(tuple(binding.record_mount_id for binding in bindings))


def components() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register(
        "counter",
        version=1,
        restore=lambda context: sl.discord.Mount(Counter(), access=Everyone(), timeout=None),
    )
    registry.register(
        "hidden-draft",
        version=1,
        restore=lambda context: sl.discord.Mount(HiddenDraft(), access=Everyone(), timeout=None),
    )
    return registry


def runtime(
    store: MemorySnapshotStore,
    frontend: FakeFrontend,
    *,
    sessions: SessionRegistry | None = None,
    clock: Callable[[], float] = time.time,
) -> DurableSessionRuntime:
    return DurableSessionRuntime(
        sessions=SessionRegistry() if sessions is None else sessions,
        components=components(),
        store=store,
        frontend=frontend,
        lease_seconds=1.0,
        maintenance_interval=0.01,
        clock=clock,
    )


async def open_counter(
    runtime: DurableSessionRuntime,
    *,
    message_id: int = 99,
    key: SessionKey | None = None,
    expires_at: float | None = None,
):
    mount = sl.discord.Mount(Counter(), access=Everyone(), timeout=None)
    result = await runtime.open(
        mount,
        delivered_to(fake_message(message_id=message_id)),
        recipe="counter",
        key=SessionKey.user("counter", 7) if key is None else key,
        actor_id=7,
        expires_at=expires_at,
    )
    return mount, result


async def test_open_publishes_one_whole_session_and_finish_deletes_it() -> None:
    store = MemorySnapshotStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        report = await tasks.start(durable.run)
        mount, result = await open_counter(durable)

        assert report.restored == ()
        assert isinstance(result, Opened)
        assert isinstance(result.session, DurableSession)
        records = await store.list_records()
        assert len(records) == 1
        record = DurableSessionCodec.loads(records[0].snapshot_payload)
        assert record.key == SessionKey.user("counter", 7)
        assert tuple(state.id for state in record.mounts) == ("root",)

        await result.session.finish()
        assert await store.list_records() == ()
        assert mount.finished
        tasks.cancel_scope.cancel()


async def test_suppressed_runtime_commit_checkpoints_hidden_component_state() -> None:
    store = MemorySnapshotStore()
    durable = runtime(store, FakeFrontend())
    component = HiddenDraft()
    mount = sl.discord.Mount(component, access=Everyone(), timeout=None)

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        opened = await durable.open(
            mount,
            delivered_to(fake_message(message_id=7)),
            recipe="hidden-draft",
            key=SessionKey.user("hidden-draft", 7),
            actor_id=7,
        )
        assert isinstance(opened, Opened)
        component.advanced = True
        mount.invalidate()

        assert await mount.refresh_now() is PresentationOutcome.UNCHANGED

        with anyio.fail_after(1):
            while True:
                stored = await store.load(opened.session.id)
                assert stored is not None
                snapshot = DurableSessionCodec.loads(stored.snapshot_payload).mounts[0].snapshot
                if snapshot.components[0].state.get("advanced") is True:
                    break
                await anyio.sleep(0)
        tasks.cancel_scope.cancel()


async def test_failed_promotion_keeps_the_durable_incumbent() -> None:
    store = MemorySnapshotStore()
    frontend = FakeFrontend()
    sessions = SessionRegistry()
    durable = runtime(store, frontend, sessions=sessions)

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, first = await open_counter(durable, message_id=1)
        assert isinstance(first, Opened)
        first_record = (await store.list_records())[0]
        frontend.reject_next = True

        _, replacement = await open_counter(durable, message_id=2)

        assert isinstance(replacement, NotDurable)
        assert await store.load(first_record.key) == first_record
        assert sessions.get(SessionKey.user("counter", 7)) == (first.session,)
        tasks.cancel_scope.cancel()


async def test_attached_mount_is_checkpointed_in_the_same_record() -> None:
    store = MemorySnapshotStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, opened = await open_counter(durable, message_id=1)
        assert isinstance(opened, Opened)
        child = sl.discord.Mount(Counter(), access=Everyone(), timeout=None)

        attached = await opened.session.attach(
            child,
            delivered_to(fake_message(message_id=2)),
            recipe="counter",
            actor_id=8,
        )

        assert isinstance(attached, Opened)
        record = DurableSessionCodec.loads((await store.list_records())[0].snapshot_payload)
        assert len(record.mounts) == 2
        assert record.mounts[1].parent_id == "root"
        assert record.mounts[1].actor_id == 8

        raw = json.loads(DurableSessionCodec.dumps(record))
        grandchild = dict(raw["mounts"][1])
        grandchild["id"] = "grandchild"
        grandchild["parent_id"] = raw["mounts"][1]["id"]
        raw["mounts"].insert(1, grandchild)
        with pytest.raises(SnapshotError, match="parents must precede"):
            DurableSessionCodec.loads(json.dumps(raw))

        raw["mounts"].pop(1)
        raw["opened_at"] = float("nan")
        with pytest.raises(SnapshotError, match="must be a number"):
            DurableSessionCodec.loads(json.dumps(raw))
        tasks.cancel_scope.cancel()


async def test_task_start_handshake_recovers_after_the_previous_runtime_releases_its_claim() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime, message_id=123)
        assert isinstance(opened, Opened)
        tasks.cancel_scope.cancel()

    second_sessions = SessionRegistry()
    second_runtime = runtime(store, FakeFrontend(), sessions=second_sessions)
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert len(report.restored) == 1
        recovered = second_sessions.get(SessionKey.user("counter", 7))
        assert len(recovered) == 1
        assert isinstance(recovered[0], DurableSession)
        tasks.cancel_scope.cancel()


async def test_remote_summaries_participate_in_distributed_cardinality() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())
    first_runtime.owner = "first"

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, first = await open_counter(first_runtime, message_id=1)
        assert isinstance(first, Opened)

        contender = runtime(store, FakeFrontend())
        contender.owner = "second"
        async with anyio.create_task_group() as contender_tasks:
            report = await contender_tasks.start(contender.run)
            assert len(report.claimed_elsewhere) == 1
            mount = sl.discord.Mount(Counter(), access=Everyone(), timeout=None)
            result = await contender.open(
                mount,
                delivered_to(fake_message(message_id=2)),
                recipe="counter",
                key=SessionKey.user("counter", 7),
                policy=SessionPolicy(protect=Unprotected()),
                actor_id=7,
            )
            assert isinstance(result, Opened)
            assert await store.load(first.session.id) is None
            contender_tasks.cancel_scope.cancel()
        tasks.cancel_scope.cancel()


async def test_corrupt_record_does_not_block_healthy_recovery() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, healthy = await open_counter(first_runtime, message_id=1, key=SessionKey.user("healthy", 7))
        _, broken = await open_counter(first_runtime, message_id=2, key=SessionKey.user("broken", 7))
        assert isinstance(healthy, Opened)
        assert isinstance(broken, Opened)
        broken_id = broken.session.id
        tasks.cancel_scope.cancel()

    stored = await store.load(broken_id)
    assert stored is not None
    token = await store.claim(broken_id, "corruptor", 1.0)
    assert token is not None
    assert await store.save(token, stored.summary_payload, "{")
    assert await store.release(token)

    second_runtime = runtime(store, FakeFrontend())
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.session_key for item in report.restored) == (SessionKey.user("healthy", 7),)
        assert tuple(item.record_key for item in report.incompatible) == (broken_id,)
        assert await store.load(broken_id) is not None
        tasks.cancel_scope.cancel()


async def test_missing_root_is_reported_and_deleted() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime)
        assert isinstance(opened, Opened)
        record_id = opened.session.id
        tasks.cancel_scope.cancel()

    second_runtime = runtime(store, FakeFrontend(missing_ids=frozenset({"root"})))
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.record_key for item in report.missing) == (record_id,)
        assert await store.load(record_id) is None
        tasks.cancel_scope.cancel()


async def test_missing_child_is_pruned_from_the_whole_session_record() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime, message_id=1)
        assert isinstance(opened, Opened)
        child = sl.discord.Mount(Counter(), access=Everyone(), timeout=None)
        attached = await opened.session.attach(
            child,
            delivered_to(fake_message(message_id=2)),
            recipe="counter",
            actor_id=8,
        )
        assert isinstance(attached, Opened)
        stored = await store.load(opened.session.id)
        assert stored is not None
        child_id = DurableSessionCodec.loads(stored.snapshot_payload).mounts[1].id
        record_id = opened.session.id
        tasks.cancel_scope.cancel()

    second_runtime = runtime(store, FakeFrontend(missing_ids=frozenset({child_id})))
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.record_key for item in report.restored) == (record_id,)
        with anyio.fail_after(1):
            while True:
                stored = await store.load(record_id)
                assert stored is not None
                if len(DurableSessionCodec.loads(stored.snapshot_payload).mounts) == 1:
                    break
                await anyio.sleep(0)
        tasks.cancel_scope.cancel()


async def test_expired_record_is_deleted_before_reconnection() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend(), clock=lambda: 0.0)

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime, expires_at=10.0)
        assert isinstance(opened, Opened)
        record_id = opened.session.id
        tasks.cancel_scope.cancel()

    second_runtime = runtime(store, FakeFrontend(), clock=lambda: 11.0)
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.record_key for item in report.expired) == (record_id,)
        assert await store.load(record_id) is None
        tasks.cancel_scope.cancel()


async def test_unreachable_record_is_retained_and_released() -> None:
    store = MemorySnapshotStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime)
        assert isinstance(opened, Opened)
        record_id = opened.session.id
        tasks.cancel_scope.cancel()

    second_runtime = runtime(store, FakeFrontend(unreachable_ids=frozenset({"root"})))
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.record_key for item in report.unreachable) == (record_id,)
        assert await store.load(record_id) is not None
        probe = await store.claim(record_id, "later-runtime", 1.0)
        assert probe is not None
        assert await store.release(probe)
        tasks.cancel_scope.cancel()
