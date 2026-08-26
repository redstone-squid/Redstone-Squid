"""Session identity, structured outcomes, cardinality, and attachment lifetime."""

import inspect
from dataclasses import fields
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

import squid_discord
import squid_layouts as sl
from squid_discord import Everyone, MountDefaults, SessionKey, SessionRegistry
from squid_discord.delivery import Abandoned
from squid_discord.sessions import (
    MembershipStatus,
    Opened,
    OpeningRequest,
    OpenResult,
    ProtectCrossUserAttachments,
    Reject,
    Rejected,
    RejectionReason,
    Replace,
    Session,
    SessionPolicy,
    Unprotected,
)
from squid_discord.testing import fake_message
from squid_layouts.primitives import Button, Heading, Row


class Panel(sl.Component):
    def render(self):
        return [Heading("Panel"), Row((Button(label="Go", on_click=self._noop, key="go"),))]

    async def _noop(self, event: sl.PressEvent) -> None:
        return None


def a_mount() -> squid_discord.Mount:
    return squid_discord.Mount(Panel(), access=Everyone(), timeout=None)


_DEFAULT_MESSAGE = object()


def to_message(message: Any = _DEFAULT_MESSAGE) -> squid_discord.Destination:
    delivered = fake_message() if message is _DEFAULT_MESSAGE else message

    async def send(
        presentation: squid_discord.presentation.DiscordPresentation,
    ) -> squid_discord.delivery.DeliveryResult:
        handle = None if delivered is None else squid_discord.delivery.handle_for(delivered)
        return squid_discord.delivery.DeliveryResult(delivered, handle)

    return send


def slowly() -> squid_discord.Destination:
    async def send(
        presentation: squid_discord.presentation.DiscordPresentation,
    ) -> squid_discord.delivery.DeliveryResult:
        await anyio.sleep(0)
        message = fake_message()
        return squid_discord.delivery.DeliveryResult(message, squid_discord.delivery.handle_for(message))

    return send


def abandoning() -> squid_discord.Destination:
    async def send(
        presentation: squid_discord.presentation.DiscordPresentation,
    ) -> squid_discord.delivery.DeliveryResult:
        raise squid_discord.delivery.DeliveryAbandoned

    return send


def failing(error: Exception) -> squid_discord.Destination:
    async def send(
        presentation: squid_discord.presentation.DiscordPresentation,
    ) -> squid_discord.delivery.DeliveryResult:
        raise error

    return send


KEY = SessionKey.user_guild("panel", 7, 42)


def test_session_keys_use_typed_frozen_scopes() -> None:
    assert SessionKey.user("account", 7).scope == squid_discord.sessions.UserScope(7)
    assert SessionKey.guild("roles", 42).scope == squid_discord.sessions.GuildScope(42)
    assert SessionKey.user_guild("settings", 7, 42).scope == squid_discord.sessions.UserGuildScope(7, 42)
    assert SessionKey.global_("status").scope == squid_discord.sessions.GlobalScope()
    assert SessionKey.custom("edit", (7, 99)).scope == squid_discord.sessions.CustomScope((7, 99))
    assert len({SessionKey.global_("status"), SessionKey.global_("status")}) == 1


def test_mount_defaults_fields_track_mount_keyword_options() -> None:
    mount_keywords = {
        name
        for name, parameter in inspect.signature(squid_discord.Mount.__init__).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }

    assert {field.name for field in fields(MountDefaults)} == mount_keywords - {"access"}


def test_mount_defaults_apply_overrides_without_mutating_the_defaults() -> None:
    defaults = MountDefaults(timeout=30, strict=True)

    mount = defaults.mount(Panel(), access=Everyone(), timeout=None)

    assert mount.timeout is None
    assert mount.strict is True
    assert defaults.timeout == 30


async def test_registry_requires_a_constructed_mount() -> None:
    registry = SessionRegistry()

    with pytest.raises(TypeError, match=r"requires a Mount; use MountDefaults\.mount or ScreenSpec\.open"):
        await registry.open(cast(Any, Panel()), to_message())


async def test_any_hashable_key_can_name_a_session() -> None:
    registry = SessionRegistry()
    key = ("panel", "team-red", 42)

    result = await registry.open(a_mount(), to_message(), key=key)

    assert isinstance(result, Opened)
    assert registry.get(key) == (result.session,)


class TestResults:
    async def test_opened_carries_the_logical_session(self) -> None:
        mount = a_mount()

        result = await SessionRegistry().open(mount, to_message(), key=KEY)

        assert isinstance(result, Opened)
        assert result.session.root is mount
        assert result.session.mounts == (mount,)

    async def test_rejected_carries_occupants_and_reason_without_delivering(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY)
        destination = AsyncMock()

        result = await registry.open(
            a_mount(),
            destination,
            key=KEY,
            policy=SessionPolicy(collision=Reject()),
        )

        assert isinstance(first, Opened)
        assert result == Rejected((first.session.snapshot,), RejectionReason.COLLISION)
        destination.assert_not_awaited()

    async def test_abandoned_is_distinct_from_rejection_and_delivery(self) -> None:
        result = await SessionRegistry().open(a_mount(), abandoning(), key=KEY)

        assert isinstance(result, Abandoned)

    async def test_handleless_delivery_is_still_opened(self) -> None:
        result = await SessionRegistry().open(a_mount(), to_message(None), key=KEY)

        assert isinstance(result, Opened)


class TestReplacement:
    async def test_same_actor_replaces_the_oldest_incumbent(self) -> None:
        registry = SessionRegistry()
        first_mount, second_mount = a_mount(), a_mount()
        first = await registry.open(first_mount, to_message(), key=KEY, actor_id=7)

        second = await registry.open(second_mount, to_message(), key=KEY, actor_id=7)

        assert isinstance(first, Opened) and isinstance(second, Opened)
        assert first_mount.finished
        assert registry.get(KEY) == (second.session,)

    async def test_incumbent_survives_a_failed_send(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY)

        with pytest.raises(RuntimeError, match="gateway"):
            await registry.open(a_mount(), failing(RuntimeError("gateway is down")), key=KEY)

        assert isinstance(first, Opened)
        assert not first.session.root.finished
        assert registry.get(KEY) == (first.session,)

    async def test_incumbent_survives_an_abandoned_send(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY)

        result = await registry.open(a_mount(), abandoning(), key=KEY)

        assert isinstance(first, Opened) and isinstance(result, Abandoned)
        assert not first.session.root.finished
        assert registry.get(KEY) == (first.session,)

    async def test_cross_user_replacement_is_protected_by_default(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)

        result = await registry.open(a_mount(), to_message(), key=KEY, actor_id=8)

        assert isinstance(first, Opened)
        assert result == Rejected((first.session.snapshot,), RejectionReason.PROTECTED)

    async def test_unprotected_policy_explicitly_allows_cross_user_replacement(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)

        result = await registry.open(
            a_mount(),
            to_message(),
            key=KEY,
            actor_id=8,
            policy=SessionPolicy(replacement=Unprotected()),
        )

        assert isinstance(first, Opened) and isinstance(result, Opened)
        assert first.session.root.finished


class TestCardinality:
    async def test_limit_greater_than_one_replaces_only_the_oldest_needed_session(self) -> None:
        registry = SessionRegistry()
        policy = SessionPolicy(limit=2, replacement=Unprotected())
        mounts = [a_mount() for _ in range(3)]
        results = [await registry.open(mount, to_message(), key=KEY, policy=policy) for mount in mounts]

        assert all(isinstance(result, Opened) for result in results)
        assert mounts[0].finished
        assert not mounts[1].finished and not mounts[2].finished
        assert tuple(session.root for session in registry.get(KEY)) == tuple(mounts[1:])

    async def test_unlimited_policy_keeps_every_session(self) -> None:
        registry = SessionRegistry()
        mounts = [a_mount() for _ in range(3)]
        for mount in mounts:
            await registry.open(mount, to_message(), key=KEY, policy=SessionPolicy(limit=None))

        assert tuple(session.root for session in registry.get(KEY)) == tuple(mounts)

    def test_non_positive_limits_are_invalid(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            SessionPolicy(limit=0)

    async def test_a_custom_collision_policy_selects_exact_victims(self) -> None:
        class ReplaceNewest:
            async def select(
                self, request: OpeningRequest, occupants: tuple[squid_discord.sessions.SessionSnapshot, ...]
            ):
                return Replace(occupants[-request.required_victims :])

        registry = SessionRegistry()
        initial = SessionPolicy(limit=2, replacement=Unprotected())
        first, second, third = a_mount(), a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY, policy=initial)
        await registry.open(second, to_message(), key=KEY, policy=initial)

        await registry.open(
            third,
            to_message(),
            key=KEY,
            policy=SessionPolicy(limit=2, collision=ReplaceNewest(), replacement=Unprotected()),
        )

        assert not first.finished and second.finished and not third.finished

    async def test_a_custom_policy_must_select_the_exact_required_victims(self) -> None:
        class SelectNobody:
            async def select(
                self, request: OpeningRequest, occupants: tuple[squid_discord.sessions.SessionSnapshot, ...]
            ):
                return Replace(())

        registry = SessionRegistry()
        await registry.open(a_mount(), to_message(), key=KEY)

        with pytest.raises(ValueError, match="exact required occupants"):
            await registry.open(
                a_mount(),
                to_message(),
                key=KEY,
                policy=SessionPolicy(collision=SelectNobody(), replacement=Unprotected()),
            )


class TestAttachments:
    async def test_finish_is_depth_first_across_the_attachment_tree(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)
        session = opened.session
        child, grandchild = a_mount(), a_mount()
        await session.attach(child, to_message())
        await session.attach(grandchild, to_message(), parent=child)
        order: list[str] = []
        grandchild.on_finish(lambda _: _note(order, "grandchild"))
        child.on_finish(lambda _: _note(order, "child"))
        session.root.on_finish(lambda _: _note(order, "root"))

        await session.finish()

        assert order == ["grandchild", "child", "root"]
        assert registry.get(KEY) == ()

    async def test_direct_root_finish_cascades_to_every_attachment(self) -> None:
        opened = await SessionRegistry().open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)
        child, sibling = a_mount(), a_mount()
        await opened.session.attach(child, to_message())
        await opened.session.attach(sibling, to_message())

        await opened.session.root.finish()

        assert child.finished and sibling.finished

    async def test_direct_child_finish_detaches_its_branch_only(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)
        child, grandchild, sibling = a_mount(), a_mount(), a_mount()
        await opened.session.attach(child, to_message())
        await opened.session.attach(grandchild, to_message(), parent=child)
        await opened.session.attach(sibling, to_message())

        await child.finish()

        assert grandchild.finished
        assert not opened.session.root.finished and not sibling.finished
        assert opened.session.mounts == (opened.session.root, sibling)
        assert registry.session_for(child) is None

    async def test_abandoned_attachment_is_not_registered(self) -> None:
        opened = await SessionRegistry().open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)
        child = a_mount()

        result = await opened.session.attach(child, abandoning())

        assert isinstance(result, Abandoned)
        assert opened.session.mounts == (opened.session.root,)

    async def test_foreign_attachment_actor_protects_replacement(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(first, Opened)
        await first.session.attach(a_mount(), to_message(), actor_id=8)

        result = await registry.open(
            a_mount(),
            to_message(),
            key=KEY,
            actor_id=7,
            policy=SessionPolicy(replacement=ProtectCrossUserAttachments()),
        )

        assert result == Rejected((first.session.snapshot,), RejectionReason.PROTECTED)

    async def test_one_unreachable_sibling_does_not_strand_the_rest(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)
        doomed, sibling = a_mount(), a_mount()
        await opened.session.attach(doomed, to_message())
        await opened.session.attach(sibling, to_message())
        doomed.finish = AsyncMock(side_effect=RuntimeError("message is gone"))  # type: ignore[method-assign]

        await opened.session.finish()

        assert sibling.finished and opened.session.root.finished
        assert registry.get(KEY) == ()


class TestMembership:
    async def test_the_opener_is_the_only_initial_member(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        assert opened.session.members == frozenset({7})
        assert opened.session.capacity is None
        assert opened.session.remaining_capacity is None

    async def test_a_root_component_finds_its_session_on_its_first_render(self) -> None:
        registry = SessionRegistry()
        seen: list[frozenset[int] | None] = []

        class Roster(sl.Component):
            def render(self):
                session = registry.session_for(mount)
                seen.append(None if session is None else session.members)
                return Heading("Roster")

        mount = squid_discord.Mount(Roster(), access=Everyone(), timeout=None)
        opened = await registry.open(mount, to_message(), key=KEY, actor_id=7)

        assert isinstance(opened, Opened)
        assert seen == [frozenset({7})]

    async def test_an_abandoned_delivery_leaves_no_mount_indexed(self) -> None:
        registry = SessionRegistry()
        mount = a_mount()

        result = await registry.open(mount, abandoning(), key=KEY, actor_id=7)

        assert isinstance(result, Abandoned)
        assert registry.session_for(mount) is None

    async def test_an_actorless_session_starts_empty(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)

        assert opened.session.members == frozenset()
        assert opened.session.participants == frozenset()

    async def test_an_attachment_actor_is_attributed_but_never_a_member(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        await opened.session.attach(a_mount(), to_message(), actor_id=8)

        assert opened.session.members == frozenset({7})
        assert opened.session.participants == frozenset({7, 8})

    async def test_join_admits_below_capacity_and_reports_the_remaining_slots(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7, capacity=3)
        assert isinstance(opened, Opened)

        result = await opened.session.join(8)

        assert result.status is MembershipStatus.JOINED
        assert result.committed
        assert result.members == frozenset({7, 8})
        assert result.remaining_capacity == 1

    async def test_rejoining_at_capacity_stays_idempotent(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7, capacity=1)
        assert isinstance(opened, Opened)

        result = await opened.session.join(7)

        assert result.status is MembershipStatus.ALREADY_MEMBER
        assert not result.committed
        assert opened.session.members == frozenset({7})

    async def test_join_at_capacity_refuses_without_mutating(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7, capacity=1)
        assert isinstance(opened, Opened)

        result = await opened.session.join(8)

        assert result.status is MembershipStatus.AT_CAPACITY
        assert result.remaining_capacity == 0
        assert opened.session.members == frozenset({7})

    async def test_a_declining_rule_refuses_without_mutating(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        result = await opened.session.join(8, when=lambda members: len(members) < 1)

        assert result.status is MembershipStatus.REFUSED
        assert opened.session.members == frozenset({7})

    async def test_leave_permits_the_opener_and_the_final_member(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        result = await opened.session.leave(7)

        assert result.status is MembershipStatus.LEFT
        assert opened.session.members == frozenset()
        assert not opened.session.root.finished
        assert registry.get(KEY) == (opened.session,)

    async def test_leaving_a_non_member_is_idempotent(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        result = await opened.session.leave(8)

        assert result.status is MembershipStatus.NOT_MEMBER
        assert opened.session.members == frozenset({7})

    async def test_membership_operations_on_a_finished_session_do_nothing(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)
        await opened.session.finish()

        joined = await opened.session.join(8)
        left = await opened.session.leave(7)

        assert joined.status is MembershipStatus.SESSION_FINISHED
        assert left.status is MembershipStatus.SESSION_FINISHED
        assert opened.session.members == frozenset({7})

    async def test_a_stale_expectation_conflicts_and_a_retry_converges(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)
        stale = opened.session.members
        await opened.session.join(8)

        conflicted = await opened.session.join(9, expect=stale)
        retried = await opened.session.join(9, expect=opened.session.members)

        assert conflicted.status is MembershipStatus.CONFLICT
        assert conflicted.members == frozenset({7, 8})
        assert retried.status is MembershipStatus.JOINED

    async def test_concurrent_joins_admit_exactly_one_into_the_final_slot(self) -> None:
        """A non-durable join never awaits inside the lock, so this guards a refactor.

        The durable path checkpoints and therefore does await; its race test is the one
        that exercises real contention.
        """
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7, capacity=2)
        assert isinstance(opened, Opened)
        results: list[MembershipStatus] = []

        async def contend(user_id: int) -> None:
            results.append((await opened.session.join(user_id)).status)

        async with anyio.create_task_group() as tasks:
            for user_id in (8, 9, 10):
                tasks.start_soon(contend, user_id)

        assert results.count(MembershipStatus.JOINED) == 1
        assert results.count(MembershipStatus.AT_CAPACITY) == 2
        assert len(opened.session.members) == 2

    async def test_a_member_protects_the_session_from_the_opener_reopening(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(first, Opened)
        await first.session.join(8)

        protected = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        unprotected = await registry.open(
            a_mount(), to_message(), key=KEY, actor_id=7, policy=SessionPolicy(replacement=Unprotected())
        )

        assert protected == Rejected((first.session.snapshot,), RejectionReason.PROTECTED)
        assert isinstance(unprotected, Opened)

    async def test_capacity_and_member_ids_are_validated(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)
        assert isinstance(opened, Opened)

        with pytest.raises(ValueError, match="positive"):
            await registry.open(a_mount(), to_message(), capacity=0)
        for bad in (True, 0, -1):
            with pytest.raises(ValueError, match="positive integers"):
                await opened.session.join(cast(int, bad))


class TestQuota:
    """Capacity caps users per session; quota caps sessions per user."""

    GUILD_ONE = SessionKey.guild("game", 1)
    GUILD_TWO = SessionKey.guild("game", 2)

    async def test_a_quota_refuses_a_second_session_in_the_same_domain(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)
        assert isinstance(first, Opened)
        second_mount = a_mount()

        second = await registry.open(second_mount, to_message(), key=self.GUILD_TWO, actor_id=7, quota=1)

        assert isinstance(second, Rejected)
        assert second.reason is RejectionReason.QUOTA_REACHED
        assert second_mount.handle is None
        assert len(tuple(registry.active())) == 1

    async def test_reopening_the_same_key_at_quota_replaces_rather_than_refuses(self) -> None:
        registry = SessionRegistry()
        first_mount, second_mount = a_mount(), a_mount()
        first = await registry.open(first_mount, to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)

        second = await registry.open(second_mount, to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)

        assert isinstance(first, Opened) and isinstance(second, Opened)
        assert first_mount.finished
        assert registry.get(self.GUILD_ONE) == (second.session,)
        assert len(tuple(registry.active())) == 1

    async def test_a_replacement_that_retires_nothing_is_still_refused(self) -> None:
        registry = SessionRegistry()
        held = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)
        assert isinstance(held, Opened)

        elsewhere = await registry.open(a_mount(), to_message(), key=self.GUILD_TWO, actor_id=7, quota=1)

        assert isinstance(elsewhere, Rejected)
        assert elsewhere.reason is RejectionReason.QUOTA_REACHED

    async def test_a_protected_incumbent_is_refused_before_the_quota_is_consulted(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)
        assert isinstance(first, Opened)

        second = await registry.open(
            a_mount(),
            to_message(),
            key=self.GUILD_ONE,
            actor_id=8,
            quota=1,
            policy=SessionPolicy(replacement=ProtectCrossUserAttachments()),
        )

        assert isinstance(second, Rejected)
        assert second.reason is RejectionReason.PROTECTED
        assert registry.get(self.GUILD_ONE) == (first.session,)

    async def test_a_quota_refuses_a_join_that_would_exceed_it(self) -> None:
        registry = SessionRegistry()
        held = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=8, quota=1)
        elsewhere = await registry.open(a_mount(), to_message(), key=self.GUILD_TWO, actor_id=9, quota=1)
        assert isinstance(held, Opened) and isinstance(elsewhere, Opened)

        result = await elsewhere.session.join(8)

        assert result.status is MembershipStatus.QUOTA_REACHED
        assert elsewhere.session.members == frozenset({9})

    async def test_leaving_one_session_frees_the_quota_for_another(self) -> None:
        registry = SessionRegistry()
        held = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=8, quota=1)
        elsewhere = await registry.open(a_mount(), to_message(), key=self.GUILD_TWO, actor_id=9, quota=1)
        assert isinstance(held, Opened) and isinstance(elsewhere, Opened)

        await held.session.leave(8)
        result = await elsewhere.session.join(8)

        assert result.status is MembershipStatus.JOINED

    async def test_a_finished_session_frees_the_quota(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)
        assert isinstance(first, Opened)
        await first.session.finish()

        second = await registry.open(a_mount(), to_message(), key=self.GUILD_TWO, actor_id=7, quota=1)

        assert isinstance(second, Opened)

    async def test_a_different_domain_does_not_count(self) -> None:
        registry = SessionRegistry()
        game = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=1)
        assert isinstance(game, Opened)

        settings = await registry.open(
            a_mount(), to_message(), key=SessionKey.guild("settings", 2), actor_id=7, quota=1
        )

        assert isinstance(settings, Opened)

    async def test_an_explicit_domain_joins_two_key_names_into_one_family(self) -> None:
        registry = SessionRegistry()
        lobby = await registry.open(
            a_mount(), to_message(), key=SessionKey.guild("lobby", 1), actor_id=7, quota=1, domain="game"
        )
        assert isinstance(lobby, Opened)

        match = await registry.open(
            a_mount(), to_message(), key=SessionKey.guild("match", 1), actor_id=7, quota=1, domain="game"
        )

        assert isinstance(match, Rejected)
        assert match.reason is RejectionReason.QUOTA_REACHED

    async def test_racing_opens_for_one_user_admit_exactly_one(self) -> None:
        """The advisory check passes in both, so only the commit-time lock can settle it."""
        registry = SessionRegistry()
        results: list[OpenResult] = []

        async def contend(key: SessionKey) -> None:
            results.append(await registry.open(a_mount(), slowly(), key=key, actor_id=7, quota=1))

        async with anyio.create_task_group() as tasks:
            for key in (self.GUILD_ONE, self.GUILD_TWO):
                tasks.start_soon(contend, key)

        assert sum(isinstance(result, Opened) for result in results) == 1
        assert sum(isinstance(result, Rejected) for result in results) == 1
        assert len(tuple(registry.active())) == 1

    async def test_racing_joins_for_one_user_admit_exactly_one(self) -> None:
        registry = SessionRegistry()
        one = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=1, quota=1)
        two = await registry.open(a_mount(), to_message(), key=self.GUILD_TWO, actor_id=2, quota=1)
        assert isinstance(one, Opened) and isinstance(two, Opened)
        statuses: list[MembershipStatus] = []

        async with anyio.create_task_group() as tasks:
            for opened in (one, two):
                tasks.start_soon(lambda target=opened: _record_join(statuses, target.session, 7))

        assert statuses.count(MembershipStatus.JOINED) == 1
        assert statuses.count(MembershipStatus.QUOTA_REACHED) == 1

    async def test_sessions_for_member_answers_what_a_user_is_in(self) -> None:
        registry = SessionRegistry()
        game = await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7)
        settings = await registry.open(a_mount(), to_message(), key=SessionKey.guild("settings", 1), actor_id=7)
        assert isinstance(game, Opened) and isinstance(settings, Opened)

        assert registry.sessions_for_member(7) == (game.session, settings.session)
        assert registry.sessions_for_member(7, domain="game") == (game.session,)
        assert registry.sessions_for_member(8) == ()

    async def test_a_quota_without_a_domain_is_refused(self) -> None:
        registry = SessionRegistry()

        with pytest.raises(ValueError, match="membership domain"):
            await registry.open(a_mount(), to_message(), actor_id=7, quota=1)
        with pytest.raises(ValueError, match="positive"):
            await registry.open(a_mount(), to_message(), key=self.GUILD_ONE, actor_id=7, quota=0)


async def _record_join(statuses: list[MembershipStatus], session: Session, user_id: int) -> None:
    statuses.append((await session.join(user_id)).status)


class TestRacesAndCleanup:
    async def test_racing_opens_leave_one_survivor(self) -> None:
        registry = SessionRegistry()
        mounts = [a_mount() for _ in range(2)]

        async with anyio.create_task_group() as tasks:
            for mount in mounts:
                tasks.start_soon(lambda candidate=mount: registry.open(candidate, slowly(), key=KEY))

        assert sum(not mount.finished for mount in mounts) == 1
        assert len(registry.get(KEY)) == 1
        assert registry._locks == {} and registry._waiting == {}

    async def test_direct_finish_cleans_up_by_session_identity(self) -> None:
        registry = SessionRegistry()
        opened = await registry.open(a_mount(), to_message(), key=KEY)
        assert isinstance(opened, Opened)

        await opened.session.root.finish()

        assert registry.get(KEY) == ()

    async def test_close_all_finishes_keyed_and_keyless_sessions(self) -> None:
        registry = SessionRegistry()
        keyed = await registry.open(a_mount(), to_message(), key=KEY)
        keyless = await registry.open(a_mount(), to_message())
        assert isinstance(keyed, Opened) and isinstance(keyless, Opened)

        await registry.close_all()

        assert keyed.session.root.finished and keyless.session.root.finished
        assert tuple(registry.active()) == ()


async def _note(order: list[str], label: str) -> None:
    order.append(label)
