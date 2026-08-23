"""Reusable per-open Discord screen policy."""

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


async def test_screen_attaches_to_a_live_parent_session() -> None:
    registry = SessionRegistry()
    root = await registry.open(Panel(), to_message(), access=Owner(7), timeout=None)
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
