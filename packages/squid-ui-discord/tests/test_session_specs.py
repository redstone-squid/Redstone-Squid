"""Reusable per-open Discord session recipe."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui.primitives import Heading
from squid_ui.text import Message
from squid_ui_discord import (
    Everyone,
    MessageRootDefaults,
    OpenContext,
    Owner,
    ScopeKind,
    SessionManager,
    SessionOptionsResolver,
    SessionSpec,
)
from squid_ui_discord.sessions import AdmissionSpec, Opened, Reject, Rejected, RejectionReason
from squid_ui_discord.testing import interaction_harness, message_harness


class Panel(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return [Heading("Panel")]


def to_message() -> squid_ui_discord.MessageDestination:
    async def send(
        payload: squid_ui_discord.message_payload.MessagePayload,
    ) -> squid_ui_discord.delivery.DeliveryResult:
        message = message_harness()
        return squid_ui_discord.delivery.DeliveryResult(message, squid_ui_discord.delivery.handle_for(message))

    return send


@pytest.mark.parametrize(
    ("scope", "open_context", "expected"),
    [
        (ScopeKind.USER, OpenContext(7, 42), squid_ui_discord.SessionKey.user("panel", 7)),
        (ScopeKind.GUILD, OpenContext(7, 42), squid_ui_discord.SessionKey.guild("panel", 42)),
        (ScopeKind.USER_GUILD, OpenContext(7, 42), squid_ui_discord.SessionKey.user_guild("panel", 7, 42)),
        (ScopeKind.GLOBAL, OpenContext(7, 42), squid_ui_discord.SessionKey.global_("panel")),
    ],
)
def test_session_spec_key_uses_its_declared_scope(
    scope: ScopeKind, open_context: OpenContext, expected: squid_ui_discord.SessionKey
) -> None:
    assert SessionSpec("panel", scope=scope).key(open_context) == expected


def test_open_context_reads_an_interaction_and_a_command_context_alike() -> None:
    """`Replyable` and `discord.Interaction` never meet, and session recipe does not care."""
    interaction = interaction_harness(user_id=7)
    interaction.guild_id = 42
    context = SimpleNamespace(author=SimpleNamespace(id=7), guild=SimpleNamespace(id=42), send=AsyncMock())

    assert OpenContext.of(interaction) == OpenContext(7, 42)
    assert OpenContext.of(cast(Any, context)) == OpenContext(7, 42)


def test_open_context_reads_a_command_context_in_a_dm_as_guildless() -> None:
    context = SimpleNamespace(author=SimpleNamespace(id=7), guild=None, send=AsyncMock())

    assert OpenContext.of(cast(Any, context)) == OpenContext(7, None)


@pytest.mark.parametrize("scope", [ScopeKind.GUILD, ScopeKind.USER_GUILD])
def test_guild_session_spec_key_requires_a_guild(scope: ScopeKind) -> None:
    with pytest.raises(TypeError, match="require an open context with a guild"):
        SessionSpec("panel", scope=scope).key(OpenContext(7))


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (OpenContext.user, squid_ui_discord.sessions.UserScope(7)),
        (OpenContext.guild, squid_ui_discord.sessions.GuildScope(42)),
        (OpenContext.user_guild, squid_ui_discord.sessions.UserGuildScope(7, 42)),
        (OpenContext.global_, squid_ui_discord.sessions.GlobalScope()),
    ],
)
def test_an_open_context_builds_each_scope_as_a_value(build: Callable[[OpenContext], object], expected: object) -> None:
    assert build(OpenContext(7, 42)) == expected


@pytest.mark.parametrize("build", [OpenContext.guild, OpenContext.user_guild])
def test_a_guild_scope_requires_an_open_context_with_a_guild(build: Callable[[OpenContext], object]) -> None:
    with pytest.raises(TypeError, match="require an open context with a guild"):
        build(OpenContext(7))


def test_a_session_key_carries_the_scope_a_pool_would_key_on() -> None:
    """The point of one taxonomy: a panel holding its key needs no conversion to reach a pool."""
    key = SessionSpec("panel", scope=ScopeKind.USER_GUILD).key(OpenContext(7, 42))

    assert key.scope == OpenContext(7, 42).user_guild()


def test_open_context_reads_discord_identity() -> None:
    interaction = interaction_harness(user_id=7)
    interaction.guild_id = 42

    assert OpenContext.of(interaction) == OpenContext(7, 42)


def test_session_spec_options_are_defensively_copied_and_read_only() -> None:
    source: squid_ui_discord.SessionOptions = {"timeout": 20}
    spec = SessionSpec("panel", options=source)
    source["timeout"] = None

    assert spec.options["timeout"] == 20

    options = cast(dict[str, object], spec.options)
    with pytest.raises(TypeError):
        options["timeout"] = None


async def test_session_spec_applies_options_overrides_and_access() -> None:
    on_error = AsyncMock()
    manager = SessionManager(MessageRootDefaults(timeout=30, strict=True, on_error=on_error))
    spec = SessionSpec("panel", access=lambda open_context: Everyone(), options={"timeout": 20})

    result = await spec.open(Panel(), to_message(), sessions=manager, open_context=OpenContext(7), timeout=None)

    assert isinstance(result, Opened)
    assert result.session.root.access == Everyone()
    assert result.session.root.timeout is None
    assert result.session.root.strict is True
    assert result.session.root.on_error is on_error
    assert result.session.key == squid_ui_discord.SessionKey.user("panel", 7)
    assert result.session.actor_for(result.session.root) == 7


async def test_session_spec_preserves_a_collision_notice() -> None:
    manager = SessionManager()
    notice = Message("This screen is already open.")
    spec = SessionSpec("panel", admission=AdmissionSpec(collision=Reject(notice=notice)))

    first = await spec.open(Panel(), to_message(), sessions=manager, open_context=OpenContext(7))
    result = await spec.open(Panel(), to_message(), sessions=manager, open_context=OpenContext(7))

    assert isinstance(first, Opened)
    assert isinstance(result, Rejected)
    assert result.notice is notice


async def test_session_spec_resolves_options_once_between_static_options_and_overrides() -> None:
    calls: list[OpenContext] = []

    async def resolve(open_context: OpenContext) -> squid_ui_discord.MessageRootOptions:
        calls.append(open_context)
        return {"timeout": 10, "strict": False}

    manager = SessionManager(MessageRootDefaults(timeout=30, strict=True))
    spec = SessionSpec("panel", options={"timeout": 20}, resolve_options=resolve)

    result = await spec.open(Panel(), to_message(), sessions=manager, open_context=OpenContext(7), strict=True)

    assert isinstance(result, Opened)
    assert calls == [OpenContext(7)]
    assert result.session.root.timeout == 10
    assert result.session.root.strict is True


async def test_session_spec_resolver_failure_does_not_construct_or_deliver_a_root() -> None:
    destination = AsyncMock()

    async def fail(_open_context: OpenContext) -> squid_ui_discord.MessageRootOptions:
        raise RuntimeError("cannot resolve spec")

    spec = SessionSpec("panel", resolve_options=fail)

    with pytest.raises(RuntimeError, match="cannot resolve spec"):
        await spec.open(Panel(), destination, sessions=SessionManager(), open_context=OpenContext(7))

    destination.assert_not_awaited()


async def test_session_spec_respond_derives_identity_and_delivery_from_the_interaction() -> None:
    manager = SessionManager(MessageRootDefaults(timeout=30))
    interaction = interaction_harness(user_id=7)
    interaction.guild_id = 42
    spec = SessionSpec("panel", scope=ScopeKind.USER_GUILD)

    result = await spec.respond(
        Panel(),
        interaction,
        sessions=manager,
        ephemeral=False,
        wait=True,
        timeout=None,
    )

    assert isinstance(result, Opened)
    assert result.session.key == squid_ui_discord.SessionKey.user_guild("panel", 7, 42)
    assert result.session.root.access == Owner(7)
    assert result.session.root.timeout is None
    assert result.session.actor_for(result.session.root) == 7
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is False
    interaction.original_response.assert_awaited_once_with()


async def test_session_spec_responds_with_an_attached_root() -> None:
    manager = SessionManager()
    root_root = manager.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await manager.open(root_root, to_message())
    assert isinstance(root, Opened)
    interaction = interaction_harness(user_id=7)
    interaction.guild_id = None

    attached = await SessionSpec("child").respond_attached(
        Panel(),
        interaction,
        sessions=manager,
        parent=root.session.root,
        timeout=None,
    )

    assert isinstance(attached, Opened)
    assert attached.session is root.session
    assert attached.session.root is root.session.root
    assert len(attached.session.message_roots) == 2


async def test_session_spec_attaches_to_a_live_parent_session() -> None:
    manager = SessionManager()
    root_root = manager.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await manager.open(root_root, to_message())
    assert isinstance(root, Opened)
    spec = SessionSpec("child", options={"timeout": None})

    attached = await spec.attach(
        Panel(), to_message(), sessions=manager, open_context=OpenContext(7), parent=root.session.root
    )

    assert isinstance(attached, Opened)
    assert attached.session is root.session
    assert len(attached.session.message_roots) == 2
    assert manager.get(spec.key(OpenContext(7))) == ()


async def test_session_spec_rejects_an_unknown_parent_without_delivery() -> None:
    manager = SessionManager()
    unknown_parent = MessageRootDefaults(timeout=None).mount(Panel(), access=Owner(7))
    spec = SessionSpec("child", options={"timeout": None})

    destination = AsyncMock()
    opened = await spec.attach(
        Panel(), destination, sessions=manager, open_context=OpenContext(7), parent=unknown_parent
    )

    assert isinstance(opened, Rejected)
    assert opened.occupants == ()
    assert opened.reason is RejectionReason.SESSION_FINISHED
    destination.assert_not_awaited()


async def test_session_spec_rejects_a_finished_parent_without_delivery() -> None:
    manager = SessionManager()
    root_root = manager.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await manager.open(root_root, to_message())
    assert isinstance(root, Opened)
    await root.session.finish()
    destination = AsyncMock()

    opened = await SessionSpec("child").attach(
        Panel(), destination, sessions=manager, open_context=OpenContext(7), parent=root_root
    )

    assert isinstance(opened, Rejected)
    assert opened.reason is RejectionReason.SESSION_FINISHED
    destination.assert_not_awaited()


async def test_session_spec_rejects_a_detached_parent_without_delivery() -> None:
    manager = SessionManager()
    root_root = manager.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await manager.open(root_root, to_message())
    assert isinstance(root, Opened)
    child_root = manager.defaults.mount(Panel(), access=Owner(7), timeout=None)
    child = await root.session.attach(child_root, to_message())
    assert isinstance(child, Opened)
    await child_root.finish()
    destination = AsyncMock()

    opened = await SessionSpec("grandchild").attach(
        Panel(), destination, sessions=manager, open_context=OpenContext(7), parent=child_root
    )

    assert isinstance(opened, Rejected)
    assert opened.reason is RejectionReason.SESSION_FINISHED
    destination.assert_not_awaited()


def test_session_spec_ergonomics_are_promoted_from_the_public_bundle() -> None:
    assert squid_ui_discord.OpenContext is OpenContext
    assert squid_ui_discord.ScopeKind is ScopeKind
    assert squid_ui_discord.SessionOptionsResolver is SessionOptionsResolver
    assert squid_ui_discord.StackNavigator is squid_ui_discord.navigation.StackNavigator


async def test_a_session_spec_carries_its_capacity_into_the_session() -> None:
    sessions = SessionManager()
    spec = SessionSpec("lobby", scope=ScopeKind.GUILD, capacity=4, access=lambda open_context: Everyone())

    opened = await spec.open(Panel(), to_message(), sessions=sessions, open_context=OpenContext(7, guild_id=5))

    assert isinstance(opened, Opened)
    assert opened.session.capacity == 4
    assert opened.session.remaining_capacity == 3


async def test_a_session_spec_carries_its_quota_and_domain() -> None:
    sessions = SessionManager()
    spec = SessionSpec("lobby", scope=ScopeKind.GUILD, quota=1, domain="game", access=lambda open_context: Everyone())

    first = await spec.open(Panel(), to_message(), sessions=sessions, open_context=OpenContext(7, guild_id=5))
    second = await spec.open(Panel(), to_message(), sessions=sessions, open_context=OpenContext(7, guild_id=6))

    assert isinstance(first, Opened)
    assert first.session.quota == 1
    assert first.session.domain == "game"
    assert isinstance(second, Rejected)
    assert second.reason is RejectionReason.QUOTA_REACHED
