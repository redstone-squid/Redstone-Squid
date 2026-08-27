"""End-to-end durable session coordination without Discord network I/O."""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import anyio
import pytest

import squid_ui as sl
import squid_ui_discord
from squid_storage import SessionRecord
from squid_ui.primitives import Text
from squid_ui.profiling import PresentationStatus
from squid_ui_discord import Everyone, SessionKey, SessionManager
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.durability import (
    ComponentRegistry,
    DurabilityHealth,
    DurableSession,
    DurableSessionCodec,
    DurableSessionRuntime,
    FrontendAddress,
    MemorySessionStore,
    MessageRootStateError,
    Missing,
    NotDurable,
    Promoted,
    Reconnected,
    RecoveredBinding,
    Unreachable,
)
from squid_ui_discord.sessions import (
    AdmissionSpec,
    MembershipStatus,
    Opened,
    Rejected,
    RejectionReason,
    Session,
    Unprotected,
)
from squid_ui_discord.testing import delivered_to, fake_message


class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return Text(f"count {self.count}")


class HiddenDraft(sl.Component):
    advanced: bool = sl.state(default=False)

    def render(self):
        return Text("Draft")


@dataclass(slots=True)
class FakeFrontend:
    reject_next: bool = False
    missing_ids: frozenset[str] = frozenset()
    unreachable_ids: frozenset[str] = frozenset()

    async def promote(self, message_root: squid_ui_discord.MessageRoot, result: DeliveryResult):
        if self.reject_next:
            self.reject_next = False
            return NotDurable("test destination is temporary")
        if result.message is None or result.handle is None or result.ephemeral:
            return NotDurable("message has no durable binding")
        await message_root.adopt_handle(result.handle)
        return Promoted(
            FrontendAddress(
                "fake",
                {"channel_id": result.message.channel.id, "message_id": result.message.id},
            ),
            result.handle,
        )

    async def reconnect(self, bindings: Sequence[RecoveredBinding]):
        missing = tuple(
            binding.record_message_root_id for binding in bindings if binding.record_message_root_id in self.missing_ids
        )
        if missing:
            return Missing(missing, tuple("test message is gone" for _ in missing))
        unreachable = tuple(
            binding.record_message_root_id
            for binding in bindings
            if binding.record_message_root_id in self.unreachable_ids
        )
        if unreachable:
            return Unreachable(unreachable, tuple("test frontend is unavailable" for _ in unreachable))
        for binding in bindings:
            message_id = binding.address.values["message_id"]
            assert isinstance(message_id, int)
            await binding.message_root.send(delivered_to(fake_message(message_id=message_id)))
        return Reconnected(tuple(binding.record_message_root_id for binding in bindings))


def components() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register(
        "counter",
        version=1,
        restore=lambda context: squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None),
    )
    registry.register(
        "hidden-draft",
        version=1,
        restore=lambda context: squid_ui_discord.MessageRoot(HiddenDraft(), access=Everyone(), timeout=None),
    )
    return registry


def runtime(
    store: MemorySessionStore,
    frontend: FakeFrontend,
    *,
    sessions: SessionManager | None = None,
    clock: Callable[[], float] = time.time,
) -> DurableSessionRuntime:
    return DurableSessionRuntime(
        sessions=SessionManager() if sessions is None else sessions,
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
    message_root = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
    result = await runtime.open(
        message_root,
        delivered_to(fake_message(message_id=message_id)),
        recipe="counter",
        key=SessionKey.user("counter", 7) if key is None else key,
        actor_id=7,
        expires_at=expires_at,
    )
    return message_root, result


async def test_open_publishes_one_whole_session_and_finish_deletes_it() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        report = await tasks.start(durable.run)
        message_root, result = await open_counter(durable)

        assert report.restored == ()
        assert isinstance(result, Opened)
        assert isinstance(result.session, DurableSession)
        records = await store.list()
        assert len(records) == 1
        record = DurableSessionCodec.loads(records[0].record_payload)
        assert record.key == SessionKey.user("counter", 7)
        assert tuple(state.id for state in record.message_roots) == ("root",)

        await result.session.finish()
        assert await store.list() == ()
        assert message_root.finished
        tasks.cancel_scope.cancel()


async def test_initial_summary_records_the_opener_as_a_participant() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, result = await open_counter(durable)

        assert isinstance(result, Opened)
        records = await store.list()
        assert json.loads(records[0].snapshot_payload)["members"] == [7]
        tasks.cancel_scope.cancel()


async def test_a_remote_summary_protects_another_user_from_replacement() -> None:
    """A second live process protects an incumbent from the summary alone."""
    store = MemorySessionStore()
    key = SessionKey.guild("counter", 5)
    first = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first.run)
        _, opened = await open_counter(first, key=key)
        assert isinstance(opened, Opened)

        # The first process keeps its claim, so the second cannot recover the record and
        # holds no live session for the key: the stored summary is its only evidence.
        second = runtime(store, FakeFrontend())
        async with anyio.create_task_group() as inner:
            report = await inner.start(second.run)
            assert len(report.claimed_elsewhere) == 1

            message_root = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
            result = await second.open(
                message_root,
                delivered_to(fake_message(message_id=100)),
                recipe="counter",
                key=key,
                actor_id=9,
            )

            assert isinstance(result, Rejected)
            assert result.reason is RejectionReason.PROTECTED
            assert result.occupants[0].participants == frozenset({7})
            assert not result.occupants[0].is_local
            assert message_root.handle is None
            inner.cancel_scope.cancel()
        tasks.cancel_scope.cancel()


async def test_purge_reports_missing_records_while_runtime_is_supervised() -> None:
    durable = runtime(MemorySessionStore(), FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)

        result = await durable.purge(("missing",))

        assert result[0].record_key == "missing"
        assert not result[0].deleted
        assert result[0].reason == "missing or claimed elsewhere"
        tasks.cancel_scope.cancel()


async def test_suppressed_runtime_commit_checkpoints_hidden_component_state() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())
    component = HiddenDraft()
    message_root = squid_ui_discord.MessageRoot(component, access=Everyone(), timeout=None)

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        opened = await durable.open(
            message_root,
            delivered_to(fake_message(message_id=7)),
            recipe="hidden-draft",
            key=SessionKey.user("hidden-draft", 7),
            actor_id=7,
        )
        assert isinstance(opened, Opened)
        component.advanced = True
        message_root.invalidate()

        assert await message_root.refresh() is PresentationStatus.UNCHANGED

        with anyio.fail_after(1):
            while True:
                stored = await store.load(opened.session.id)
                assert stored is not None
                snapshot = DurableSessionCodec.loads(stored.record_payload).message_roots[0].state
                if snapshot.components[0].state.get("advanced") is True:
                    break
                await anyio.sleep(0)
        tasks.cancel_scope.cancel()


async def test_failed_promotion_keeps_the_durable_incumbent() -> None:
    store = MemorySessionStore()
    frontend = FakeFrontend()
    sessions = SessionManager()
    durable = runtime(store, frontend, sessions=sessions)

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, first = await open_counter(durable, message_id=1)
        assert isinstance(first, Opened)
        first_record = (await store.list())[0]
        frontend.reject_next = True

        _, replacement = await open_counter(durable, message_id=2)

        assert isinstance(replacement, NotDurable)
        assert await store.load(first_record.key) == first_record
        assert sessions.get(SessionKey.user("counter", 7)) == (first.session,)
        tasks.cancel_scope.cancel()


async def test_attached_message_root_is_checkpointed_in_the_same_record() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, opened = await open_counter(durable, message_id=1)
        assert isinstance(opened, Opened)
        child = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)

        attached = await opened.session.attach(
            child,
            delivered_to(fake_message(message_id=2)),
            recipe="counter",
            actor_id=8,
        )

        assert isinstance(attached, Opened)
        record = DurableSessionCodec.loads((await store.list())[0].record_payload)
        assert len(record.message_roots) == 2
        assert record.message_roots[1].parent_id == "root"
        assert record.message_roots[1].actor_id == 8

        raw = json.loads(DurableSessionCodec.dumps(record))
        grandchild = dict(raw["message_roots"][1])
        grandchild["id"] = "grandchild"
        grandchild["parent_id"] = raw["message_roots"][1]["id"]
        raw["message_roots"].insert(1, grandchild)
        with pytest.raises(MessageRootStateError, match="parents must precede"):
            DurableSessionCodec.loads(json.dumps(raw))

        raw["message_roots"].pop(1)
        raw["opened_at"] = float("nan")
        with pytest.raises(MessageRootStateError, match="must be a number"):
            DurableSessionCodec.loads(json.dumps(raw))
        tasks.cancel_scope.cancel()


async def test_task_start_handshake_recovers_after_the_previous_runtime_releases_its_claim() -> None:
    store = MemorySessionStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime, message_id=123)
        assert isinstance(opened, Opened)
        tasks.cancel_scope.cancel()

    second_sessions = SessionManager()
    second_runtime = runtime(store, FakeFrontend(), sessions=second_sessions)
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert len(report.restored) == 1
        recovered = second_sessions.get(SessionKey.user("counter", 7))
        assert len(recovered) == 1
        assert isinstance(recovered[0], DurableSession)
        tasks.cancel_scope.cancel()


async def test_remote_summaries_participate_in_distributed_cardinality() -> None:
    store = MemorySessionStore()
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
            message_root = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
            result = await contender.open(
                message_root,
                delivered_to(fake_message(message_id=2)),
                recipe="counter",
                key=SessionKey.user("counter", 7),
                admission=AdmissionSpec(replacement=Unprotected()),
                actor_id=7,
            )
            assert isinstance(result, Opened)
            assert await store.load(first.session.id) is None
            contender_tasks.cancel_scope.cancel()
        tasks.cancel_scope.cancel()


async def test_corrupt_record_does_not_block_healthy_recovery() -> None:
    store = MemorySessionStore()
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
    assert await store.save(token, stored.snapshot_payload, "{")
    assert await store.release(token)

    second_runtime = runtime(store, FakeFrontend())
    async with anyio.create_task_group() as tasks:
        report = await tasks.start(second_runtime.run)

        assert tuple(item.session_key for item in report.restored) == (SessionKey.user("healthy", 7),)
        assert tuple(item.record_key for item in report.incompatible) == (broken_id,)
        assert await store.load(broken_id) is not None
        tasks.cancel_scope.cancel()


async def test_missing_root_is_reported_and_deleted() -> None:
    store = MemorySessionStore()
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
    store = MemorySessionStore()
    first_runtime = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(first_runtime.run)
        _, opened = await open_counter(first_runtime, message_id=1)
        assert isinstance(opened, Opened)
        child = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
        attached = await opened.session.attach(
            child,
            delivered_to(fake_message(message_id=2)),
            recipe="counter",
            actor_id=8,
        )
        assert isinstance(attached, Opened)
        stored = await store.load(opened.session.id)
        assert stored is not None
        child_id = DurableSessionCodec.loads(stored.record_payload).message_roots[1].id
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
                if len(DurableSessionCodec.loads(stored.record_payload).message_roots) == 1:
                    break
                await anyio.sleep(0)
        tasks.cancel_scope.cancel()


async def test_expired_record_is_deleted_before_reconnection() -> None:
    store = MemorySessionStore()
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
    store = MemorySessionStore()
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


async def test_a_durable_join_is_checkpointed_and_survives_recovery() -> None:
    store = MemorySessionStore()
    key = SessionKey.guild("counter", 5)

    async with anyio.create_task_group() as tasks:
        first = runtime(store, FakeFrontend())
        await tasks.start(first.run)
        message_root = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
        opened = await first.open(
            message_root,
            delivered_to(fake_message(message_id=99)),
            recipe="counter",
            key=key,
            actor_id=7,
            capacity=3,
        )
        assert isinstance(opened, Opened)

        assert (await opened.session.join(8)).status is MembershipStatus.JOINED

        # Checkpointed by the join itself, with no maintenance sweep in between.
        payload = (await store.list())[0].record_payload
        raw = json.loads(payload)
        assert raw["protocol"] == DurableSessionCodec.protocol
        assert {"state", "address"} <= raw["message_roots"][0].keys()
        assert "snapshot" not in raw["message_roots"][0]
        assert "locator" not in raw["message_roots"][0]
        record = DurableSessionCodec.loads(payload)
        assert record.members == frozenset({7, 8})
        assert record.capacity == 3
        assert record.protocol == DurableSessionCodec.protocol
        tasks.cancel_scope.cancel()

    async with anyio.create_task_group() as tasks:
        second = runtime(store, FakeFrontend())
        report = await tasks.start(second.run)

        assert len(report.restored) == 1
        recovered = next(iter(second.sessions.active()))
        assert recovered.members == frozenset({7, 8})
        assert recovered.capacity == 3
        assert recovered.remaining_capacity == 1
        tasks.cancel_scope.cancel()


async def test_a_recovered_attachment_actor_is_attributed_but_not_a_member() -> None:
    store = MemorySessionStore()
    key = SessionKey.guild("counter", 5)

    async with anyio.create_task_group() as tasks:
        first = runtime(store, FakeFrontend())
        await tasks.start(first.run)
        message_root = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
        opened = await first.open(
            message_root, delivered_to(fake_message(message_id=99)), recipe="counter", key=key, actor_id=7
        )
        assert isinstance(opened, Opened)
        child = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)
        await opened.session.attach(child, delivered_to(fake_message(message_id=100)), recipe="counter", actor_id=8)
        tasks.cancel_scope.cancel()

    async with anyio.create_task_group() as tasks:
        second = runtime(store, FakeFrontend())
        report = await tasks.start(second.run)

        assert len(report.restored) == 1
        recovered = next(iter(second.sessions.active()))
        assert recovered.members == frozenset({7})
        assert recovered.participants == frozenset({7, 8})
        tasks.cancel_scope.cancel()


async def test_a_membership_checkpoint_that_loses_the_claim_finishes_without_deadlocking() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, opened = await open_counter(durable)
        assert isinstance(opened, Opened)
        # The claim is gone, so the checkpoint the join triggers loses it — and losing it
        # finishes the session, which needs the lifecycle lock the join has released.
        store.save = _fenced_out  # type: ignore[method-assign]

        with anyio.fail_after(1):
            result = await opened.session.join(8)

        assert result.status is MembershipStatus.JOINED
        assert opened.session.root.finished
        tasks.cancel_scope.cancel()


async def test_a_failed_membership_checkpoint_leaves_the_session_dirty_and_usable() -> None:
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, opened = await open_counter(durable)
        assert isinstance(opened, Opened)
        session = opened.session
        assert isinstance(session, DurableSession)

        broken = store.save
        store.save = _raising  # type: ignore[method-assign]
        try:
            result = await session.join(8)
        finally:
            store.save = broken  # type: ignore[method-assign]

        assert result.status is MembershipStatus.JOINED
        assert session.members == frozenset({7, 8})
        assert session.health is DurabilityHealth.CHECKPOINT_PENDING

        await durable.flush()
        record = DurableSessionCodec.loads((await store.list())[0].record_payload)
        assert record.members == frozenset({7, 8})
        assert session.health is DurabilityHealth.HEALTHY
        tasks.cancel_scope.cancel()


async def _raising(*args: object, **kwargs: object) -> bool:
    message = "store is unavailable"
    raise RuntimeError(message)


async def _fenced_out(*args: object, **kwargs: object) -> bool:
    return False


async def test_a_record_without_membership_is_refused() -> None:
    store = MemorySessionStore()

    async with anyio.create_task_group() as tasks:
        first = runtime(store, FakeFrontend())
        await tasks.start(first.run)
        _, opened = await open_counter(first)
        assert isinstance(opened, Opened)
        tasks.cancel_scope.cancel()

    stored = (await store.list())[0]
    record = json.loads(stored.record_payload)
    del record["members"]
    store._records[stored.key] = SessionRecord(stored.key, stored.scope, stored.snapshot_payload, json.dumps(record))

    async with anyio.create_task_group() as tasks:
        second = runtime(store, FakeFrontend())
        report = await tasks.start(second.run)

        assert len(report.incompatible) == 1
        tasks.cancel_scope.cancel()


async def test_a_snapshot_disagreeing_with_its_record_is_refused() -> None:
    store = MemorySessionStore()

    async with anyio.create_task_group() as tasks:
        first = runtime(store, FakeFrontend())
        await tasks.start(first.run)
        _, opened = await open_counter(first)
        assert isinstance(opened, Opened)
        tasks.cancel_scope.cancel()

    stored = (await store.list())[0]
    snapshot = json.loads(stored.snapshot_payload)
    snapshot["members"] = [7, 8]
    store._records[stored.key] = SessionRecord(stored.key, stored.scope, json.dumps(snapshot), stored.record_payload)

    async with anyio.create_task_group() as tasks:
        second = runtime(store, FakeFrontend())
        report = await tasks.start(second.run)

        assert len(report.incompatible) == 1
        assert "does not match" in report.incompatible[0].reason
        tasks.cancel_scope.cancel()


async def test_a_durable_quota_survives_recovery() -> None:
    store = MemorySessionStore()
    key = SessionKey.guild("game", 5)

    async with anyio.create_task_group() as tasks:
        first = runtime(store, FakeFrontend())
        await tasks.start(first.run)
        opened = await first.open(
            squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None),
            delivered_to(fake_message(message_id=99)),
            recipe="counter",
            key=key,
            actor_id=7,
            quota=1,
            domain="game",
        )
        assert isinstance(opened, Opened)
        record = DurableSessionCodec.loads((await store.list())[0].record_payload)
        assert record.quota == 1
        assert record.domain == "game"
        tasks.cancel_scope.cancel()

    async with anyio.create_task_group() as tasks:
        second = runtime(store, FakeFrontend())
        report = await tasks.start(second.run)

        assert len(report.restored) == 1
        recovered = next(iter(second.sessions.active()))
        assert recovered.quota == 1
        assert recovered.domain == "game"
        # The recovered session counts, so the same user cannot open a second game.
        blocked = await second.open(
            squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None),
            delivered_to(fake_message(message_id=100)),
            recipe="counter",
            key=SessionKey.guild("game", 6),
            actor_id=7,
            quota=1,
            domain="game",
        )
        assert isinstance(blocked, Rejected)
        assert blocked.reason is RejectionReason.QUOTA_REACHED
        tasks.cancel_scope.cancel()


async def test_attaching_a_durable_session_without_a_recipe_is_refused_not_raised() -> None:
    """`SessionManager.session_for` hands back a plain `Session`.

    Every caller that reaches a session that way -- `SessionSpec.attach` among them -- calls
    `attach` with the base class's arguments, which for a durable session used to be a missing
    required keyword and a `TypeError` at runtime.
    """
    store = MemorySessionStore()
    durable = runtime(store, FakeFrontend())

    async with anyio.create_task_group() as tasks:
        await tasks.start(durable.run)
        _, opened = await open_counter(durable, message_id=1)
        assert isinstance(opened, Opened)
        session: Session = opened.session
        child = squid_ui_discord.MessageRoot(Counter(), access=Everyone(), timeout=None)

        refused = await session.attach(child, delivered_to(fake_message(message_id=2)), actor_id=8)

        assert isinstance(refused, Rejected)
        assert refused.reason is RejectionReason.RECIPE_REQUIRED
        tasks.cancel_scope.cancel()
