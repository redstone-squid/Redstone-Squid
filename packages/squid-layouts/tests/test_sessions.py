"""Session identity, structured outcomes, cardinality, and attachment lifetime."""

import inspect
from dataclasses import fields
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, MountDefaults, SessionKey, SessionRegistry
from squid_layouts.discord.delivery import Abandoned
from squid_layouts.discord.sessions import (
    Opened,
    OpeningRequest,
    ProtectCrossUserAttachments,
    Reject,
    Rejected,
    RejectionReason,
    Replace,
    SessionPolicy,
    Unprotected,
)
from squid_layouts.discord.testing import fake_message
from squid_layouts.primitives import Button, Heading, Row


class Panel(sl.Component):
    def render(self):
        return [Heading("Panel"), Row((Button(label="Go", on_click=self._noop, key="go"),))]

    async def _noop(self, event: sl.PressEvent) -> None:
        return None


def a_mount() -> sl.discord.Mount:
    return sl.discord.Mount(Panel(), access=Everyone(), timeout=None)


_DEFAULT_MESSAGE = object()


def to_message(message: Any = _DEFAULT_MESSAGE) -> sl.discord.Destination:
    delivered = fake_message() if message is _DEFAULT_MESSAGE else message

    async def send(presentation: sl.discord.presentation.DiscordPresentation) -> sl.discord.delivery.DeliveryReceipt:
        handle = None if delivered is None else sl.discord.delivery.handle_for(delivered)
        return sl.discord.delivery.DeliveryReceipt(delivered, handle)

    return send


def slowly() -> sl.discord.Destination:
    async def send(presentation: sl.discord.presentation.DiscordPresentation) -> sl.discord.delivery.DeliveryReceipt:
        await anyio.sleep(0)
        message = fake_message()
        return sl.discord.delivery.DeliveryReceipt(message, sl.discord.delivery.handle_for(message))

    return send


def abandoning() -> sl.discord.Destination:
    async def send(presentation: sl.discord.presentation.DiscordPresentation) -> sl.discord.delivery.DeliveryReceipt:
        raise sl.discord.delivery.DeliveryAbandoned

    return send


def failing(error: Exception) -> sl.discord.Destination:
    async def send(presentation: sl.discord.presentation.DiscordPresentation) -> sl.discord.delivery.DeliveryReceipt:
        raise error

    return send


KEY = SessionKey.user_guild("panel", 7, 42)


def test_session_keys_use_typed_frozen_scopes() -> None:
    assert SessionKey.user("account", 7).scope == sl.discord.sessions.UserScope(7)
    assert SessionKey.guild("roles", 42).scope == sl.discord.sessions.GuildScope(42)
    assert SessionKey.user_guild("settings", 7, 42).scope == sl.discord.sessions.UserGuildScope(7, 42)
    assert SessionKey.global_("status").scope == sl.discord.sessions.GlobalScope()
    assert SessionKey.custom("edit", (7, 99)).scope == sl.discord.sessions.CustomScope((7, 99))
    assert len({SessionKey.global_("status"), SessionKey.global_("status")}) == 1


def test_mount_defaults_fields_track_mount_keyword_options() -> None:
    mount_keywords = {
        name
        for name, parameter in inspect.signature(sl.discord.Mount.__init__).parameters.items()
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

    with pytest.raises(TypeError, match=r"requires a Mount; use MountDefaults\.mount or Screen\.open"):
        await registry.open(cast(Any, Panel()), to_message())


async def test_any_hashable_key_can_name_a_session() -> None:
    registry = SessionRegistry()
    key = ("panel", "team-red", 42)

    result = await registry.open(a_mount(), to_message(), key=key)

    assert isinstance(result, Opened)
    assert registry.get(key) == (result.session,)


class TestOutcomes:
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
        assert result == Rejected((first.session.summary,), RejectionReason.COLLISION)
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
        assert result == Rejected((first.session.summary,), RejectionReason.PROTECTED)

    async def test_unprotected_policy_explicitly_allows_cross_user_replacement(self) -> None:
        registry = SessionRegistry()
        first = await registry.open(a_mount(), to_message(), key=KEY, actor_id=7)

        result = await registry.open(
            a_mount(),
            to_message(),
            key=KEY,
            actor_id=8,
            policy=SessionPolicy(protect=Unprotected()),
        )

        assert isinstance(first, Opened) and isinstance(result, Opened)
        assert first.session.root.finished


class TestCardinality:
    async def test_limit_greater_than_one_replaces_only_the_oldest_needed_session(self) -> None:
        registry = SessionRegistry()
        policy = SessionPolicy(limit=2, protect=Unprotected())
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
            async def select(self, request: OpeningRequest, occupants: tuple[sl.discord.sessions.SessionSummary, ...]):
                return Replace(occupants[-request.required_victims :])

        registry = SessionRegistry()
        initial = SessionPolicy(limit=2, protect=Unprotected())
        first, second, third = a_mount(), a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY, policy=initial)
        await registry.open(second, to_message(), key=KEY, policy=initial)

        await registry.open(
            third,
            to_message(),
            key=KEY,
            policy=SessionPolicy(limit=2, collision=ReplaceNewest(), protect=Unprotected()),
        )

        assert not first.finished and second.finished and not third.finished

    async def test_a_custom_policy_must_select_the_exact_required_victims(self) -> None:
        class SelectNobody:
            async def select(self, request: OpeningRequest, occupants: tuple[sl.discord.sessions.SessionSummary, ...]):
                return Replace(())

        registry = SessionRegistry()
        await registry.open(a_mount(), to_message(), key=KEY)

        with pytest.raises(ValueError, match="exact required occupants"):
            await registry.open(
                a_mount(),
                to_message(),
                key=KEY,
                policy=SessionPolicy(collision=SelectNobody(), protect=Unprotected()),
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
            policy=SessionPolicy(protect=ProtectCrossUserAttachments()),
        )

        assert result == Rejected((first.session.summary,), RejectionReason.PROTECTED)

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
