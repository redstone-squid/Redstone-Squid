"""Reusable per-open Discord screen policy."""

from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, MountDefaults, Owner, Screen, SessionRegistry
from squid_layouts.discord.screens import Opener, Scope
from squid_layouts.discord.sessions import Opened
from squid_layouts.discord.testing import fake_interaction, fake_message
from squid_layouts.primitives import Heading


class Panel(sl.Component):
    def render(self):
        return [Heading("Panel")]


def to_message() -> sl.discord.Destination:
    async def send(presentation: sl.discord.presentation.DiscordPresentation) -> sl.discord.delivery.DeliveryReceipt:
        message = fake_message()
        return sl.discord.delivery.DeliveryReceipt(message, sl.discord.delivery.handle_for(message))

    return send


@pytest.mark.parametrize(
    ("scope", "opener", "expected"),
    [
        (Scope.USER, Opener(7, 42), sl.discord.SessionKey.user("panel", 7)),
        (Scope.GUILD, Opener(7, 42), sl.discord.SessionKey.guild("panel", 42)),
        (Scope.USER_GUILD, Opener(7, 42), sl.discord.SessionKey.user_guild("panel", 7, 42)),
        (Scope.GLOBAL, Opener(7, 42), sl.discord.SessionKey.global_("panel")),
    ],
)
def test_screen_key_uses_its_declared_scope(scope: Scope, opener: Opener, expected: sl.discord.SessionKey) -> None:
    assert Screen("panel", scope=scope).key(opener) == expected


@pytest.mark.parametrize("scope", [Scope.GUILD, Scope.USER_GUILD])
def test_guild_screen_key_requires_a_guild(scope: Scope) -> None:
    with pytest.raises(TypeError, match="require an opener with a guild"):
        Screen("panel", scope=scope).key(Opener(7))


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (Opener.user, sl.discord.sessions.UserScope(7)),
        (Opener.guild, sl.discord.sessions.GuildScope(42)),
        (Opener.user_guild, sl.discord.sessions.UserGuildScope(7, 42)),
        (Opener.global_, sl.discord.sessions.GlobalScope()),
    ],
)
def test_an_opener_builds_each_scope_as_a_value(build: Callable[[Opener], object], expected: object) -> None:
    assert build(Opener(7, 42)) == expected


@pytest.mark.parametrize("build", [Opener.guild, Opener.user_guild])
def test_a_guild_scope_requires_an_opener_with_a_guild(build: Callable[[Opener], object]) -> None:
    with pytest.raises(TypeError, match="require an opener with a guild"):
        build(Opener(7))


def test_a_session_key_carries_the_scope_a_pool_would_key_on() -> None:
    """The point of one taxonomy: a panel holding its key needs no conversion to reach a pool."""
    key = Screen("panel", scope=Scope.USER_GUILD).key(Opener(7, 42))

    assert key.scope == Opener(7, 42).user_guild()


def test_opener_reads_discord_identity() -> None:
    interaction = fake_interaction(user_id=7)
    interaction.guild_id = 42

    assert Opener.of(interaction) == Opener(7, 42)


def test_screen_options_are_defensively_copied_and_read_only() -> None:
    source: dict[str, object] = {"timeout": 20}
    screen = Screen("panel", options=source)
    source["timeout"] = None

    assert screen.options["timeout"] == 20

    options = cast(dict[str, object], screen.options)
    with pytest.raises(TypeError):
        options["timeout"] = None


async def test_screen_applies_options_overrides_and_access() -> None:
    on_error = AsyncMock()
    registry = SessionRegistry(MountDefaults(timeout=30, strict=True, on_error=on_error))
    screen = Screen("panel", access=lambda opener: Everyone(), options={"timeout": 20})

    result = await screen.open(registry, Panel(), to_message(), opener=Opener(7), timeout=None)

    assert isinstance(result, Opened)
    assert result.session.root.access == Everyone()
    assert result.session.root.timeout is None
    assert result.session.root.strict is True
    assert result.session.root.on_error is on_error
    assert result.session.key == sl.discord.SessionKey.user("panel", 7)
    assert result.session.actor_for(result.session.root) == 7


async def test_screen_respond_derives_identity_and_delivery_from_the_interaction() -> None:
    registry = SessionRegistry(MountDefaults(timeout=30))
    interaction = fake_interaction(user_id=7)
    interaction.guild_id = 42
    screen = Screen("panel", scope=Scope.USER_GUILD)

    result = await screen.respond(
        registry,
        Panel(),
        interaction,
        ephemeral=False,
        wait=True,
        timeout=None,
    )

    assert isinstance(result, Opened)
    assert result.session.key == sl.discord.SessionKey.user_guild("panel", 7, 42)
    assert result.session.root.access == Owner(7)
    assert result.session.root.timeout is None
    assert result.session.actor_for(result.session.root) == 7
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is False
    interaction.original_response.assert_awaited_once_with()


async def test_screen_respond_forwards_parent_attachment() -> None:
    registry = SessionRegistry()
    root_mount = registry.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await registry.open(root_mount, to_message())
    assert isinstance(root, Opened)
    interaction = fake_interaction(user_id=7)
    interaction.guild_id = None

    attached = await Screen("child").respond(
        registry,
        Panel(),
        interaction,
        parent=root.session.root,
        timeout=None,
    )

    assert isinstance(attached, Opened)
    assert attached.session is root.session
    assert attached.session.root is root.session.root
    assert len(attached.session.mounts) == 2


async def test_screen_attaches_to_a_live_parent_session() -> None:
    registry = SessionRegistry()
    root_mount = registry.defaults.mount(Panel(), access=Owner(7), timeout=None)
    root = await registry.open(root_mount, to_message())
    assert isinstance(root, Opened)
    screen = Screen("child", options={"timeout": None})

    attached = await screen.open(registry, Panel(), to_message(), opener=Opener(7), parent=root.session.root)

    assert isinstance(attached, Opened)
    assert attached.session is root.session
    assert len(attached.session.mounts) == 2
    assert registry.get(screen.key(Opener(7))) == ()


async def test_screen_opens_a_root_when_parent_has_no_live_session() -> None:
    registry = SessionRegistry()
    unknown_parent = MountDefaults(timeout=None).mount(Panel(), access=Owner(7))
    screen = Screen("child", options={"timeout": None})

    opened = await screen.open(registry, Panel(), to_message(), opener=Opener(7), parent=unknown_parent)

    assert isinstance(opened, Opened)
    assert opened.session.key == screen.key(Opener(7))


async def test_a_screen_carries_its_capacity_into_the_session() -> None:
    sessions = SessionRegistry()
    screen = Screen("lobby", scope=Scope.GUILD, capacity=4, access=lambda opener: Everyone())

    opened = await screen.open(sessions, Panel(), to_message(), opener=Opener(7, guild_id=5))

    assert isinstance(opened, Opened)
    assert opened.session.capacity == 4
    assert opened.session.remaining_capacity == 3
