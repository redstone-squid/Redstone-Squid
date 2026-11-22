"""Message context actions and their live Redstoner workspace."""

import asyncio
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_ui.ext.discord as sd
from squid.bot.admin_cog import Admin
from squid.bot.give_redstoner_cog import RedstonerScreen
from squid.bot.utils.permissions import Grants
from squid.core.permissions.domain import PermissionNode
from squid.core.permissions.domain.catalogue import REDSTONER_PANEL_MANAGE
from squid_ui.ext.discord.testing import AsyncCallRecorder
from squid_ui.testing import RecordingResponder, labels, press, press_event


@dataclass
class ArchivedMessage:
    add_reaction: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass
class Channel:
    send: AsyncCallRecorder


@dataclass(frozen=True)
class Author:
    id: int
    mention: str


@dataclass
class Attachment:
    to_file: AsyncCallRecorder = field(default_factory=lambda: AsyncCallRecorder(result="upload"))


@dataclass(frozen=True)
class Reaction:
    emoji: str
    count: int


@dataclass
class Message:
    author: Author
    reactions: tuple[Reaction, ...]
    channel: Channel
    clean_content: str
    embeds: tuple[str, ...]
    attachments: tuple[Attachment, ...]
    stickers: tuple[discord.StickerItem, ...]
    delete: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass(frozen=True)
class User:
    id: int
    name: str


class Bot:
    def get_user(self, user_id: int) -> User:
        assert user_id == 7
        return User(7, "builder")


async def test_archive_copies_attachments_and_deletes_the_original() -> None:
    archived = ArchivedMessage()
    channel = Channel(send=AsyncCallRecorder(result=archived))
    author = Author(id=7, mention="<@7>")
    attachment = Attachment()
    message = Message(
        author=author,
        reactions=(Reaction("⭐", 3), Reaction("🔥", 2)),
        channel=channel,
        clean_content="A useful old message",
        embeds=("embed",),
        attachments=(attachment,),
        stickers=(cast(discord.StickerItem, SimpleNamespace(url="https://cdn.discordapp.com/stickers/123.png")),),
    )
    cog = Admin.__new__(Admin)
    cog.bot = cast(Any, Bot())

    interaction = Mock(spec=discord.Interaction)
    interaction.edit_original_response = AsyncCallRecorder(result=archived)
    await cog._archive_message(cast(discord.Message, message), interaction)

    attachment.to_file.assert_awaited_once_with()
    assert channel.send.await_args is None
    call = interaction.edit_original_response.await_args
    assert call is not None
    assert "⭐:3 🔥:2" in call.kwargs["content"]
    assert "A useful old message" in call.kwargs["content"]
    assert call.kwargs["attachments"] == ["upload"]
    assert "https://cdn.discordapp.com/stickers/123.png" in call.kwargs["content"]
    assert "<@7> (builder `<@7>`) wrote:" in call.kwargs["content"]
    archived.add_reaction.assert_awaited_once_with("❌")
    message.delete.assert_awaited_once_with()


class AsyncAuthorizer:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self, _node: PermissionNode) -> bool:
        self.calls += 1
        return self.result


class AsyncAction:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


def make_redstoner_screen(
    *,
    can_deploy: bool = True,
    allowed: bool = True,
) -> tuple[RedstonerScreen, AsyncAuthorizer, AsyncAction]:
    authorizer = AsyncAuthorizer(result=allowed)
    publisher = AsyncAction()
    return (
        RedstonerScreen(
            guild_id=1,
            role_id=2,
            source_channel_id=3,
            grants=Grants(frozenset({REDSTONER_PANEL_MANAGE} if can_deploy else ()), authorizer),
            publish_panel=publisher,
        ),
        authorizer,
        publisher,
    )


def test_redstoner_screen_is_a_private_user_guild_workspace() -> None:
    screen, _, _ = make_redstoner_screen()

    assert isinstance(screen.__response_spec__.session, sd.SessionSpec)
    assert screen.__response_spec__.session.scope is sd.ScopeKind.USER_GUILD
    assert screen.__response_spec__.audience == "personal"
    assert screen.__response_spec__.timeout == 300
    assert {"Deploy role controls"} <= set(labels(screen.render()))


def test_redstoner_hides_deployment_without_initial_permission() -> None:
    screen, _, _ = make_redstoner_screen(can_deploy=False)

    assert "Deploy role controls" not in labels(screen.render())


async def test_redstoner_rechecks_permission_before_deployment() -> None:
    screen, authorize, publish = make_redstoner_screen(allowed=False)
    responder = RecordingResponder()

    await screen._deploy(press_event(responder=responder))

    assert authorize.calls == 1
    assert publish.calls == 0
    assert len(responder.notices) == 1


async def test_redstoner_deploys_after_permission_recheck() -> None:
    screen, authorize, publish = make_redstoner_screen()
    responder = RecordingResponder()

    await press(screen, "deploy", responder=responder)

    assert authorize.calls == 1
    assert publish.calls == 1
    assert len(responder.notices) == 1


async def test_paste_rechecks_access_to_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("squid.bot.admin_cog.enforce", AsyncMock())
    source = Mock(spec=discord.TextChannel)
    source.permissions_for.return_value = discord.Permissions(read_message_history=True)
    source.fetch_message = AsyncMock()
    selected = SimpleNamespace(channel=source, id=123)
    request = SimpleNamespace(
        guild=SimpleNamespace(id=1, fetch_member=AsyncMock()),
        user=SimpleNamespace(id=7),
        channel=Mock(spec=discord.TextChannel),
    )
    owner = SimpleNamespace(_pending_archives={(1, 7): selected}, _paste_lock=asyncio.Lock())
    callback = inspect.unwrap(cast(Any, Admin).paste.callback)

    await callback(owner, request)

    source.fetch_message.assert_not_awaited()
    assert owner._pending_archives == {(1, 7): selected}
