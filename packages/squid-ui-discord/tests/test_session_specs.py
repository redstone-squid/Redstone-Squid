"""Reusable per-open Discord session recipe."""

from collections.abc import Callable
from typing import Any, cast, override

import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui_discord import (
    Everyone,
    OpenContext,
    ScopeKind,
    SessionSpec,
)
from squid_ui_discord.testing import ContextHarness, interaction_harness, message_harness


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
    """Both native source shapes must produce the same user and guild identity."""
    interaction = interaction_harness(user_id=7)
    interaction.guild_id = 42
    context = ContextHarness(message=message_harness(guild_id=42), user_id=7)
    context.guild = context.message.guild

    assert OpenContext.of(interaction) == OpenContext(7, 42)
    assert OpenContext.of(cast(Any, context)) == OpenContext(7, 42)


def test_open_context_reads_a_command_context_in_a_dm_as_guildless() -> None:
    context = ContextHarness(message=message_harness(guild_id=None), user_id=7)

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


@pytest.mark.parametrize("name", [None, ""])
def test_keys_require_a_resolved_nonempty_session_name(name: str | None) -> None:
    with pytest.raises(ValueError, match="resolved non-empty name"):
        SessionSpec(name).key(OpenContext(7, 42))


async def test_session_policy_is_applied_through_the_scope_opening_path() -> None:
    class Panel(squid_ui_discord.Screen):
        policy = squid_ui_discord.ResponseSpec(
            session=SessionSpec("lobby", capacity=4, quota=1, domain="games"),
            access=Everyone(),
            timeout=20,
        )

        @override
        def render(self) -> sl.LayoutNode:
            return sl.heading("Lobby")

    async with squid_ui_discord.testing.stage(Panel(), owner=object(), user_id=7, timeout=None) as ui:
        session = ui.result.session
        assert session is not None
        assert session.capacity == 4
        assert session.remaining_capacity == 3
        assert session.quota == 1
        assert session.domain == "games"
        assert session.key == squid_ui_discord.SessionKey.user("lobby", 7)
        assert ui.root.access == Everyone()
        assert ui.root.timeout is None
