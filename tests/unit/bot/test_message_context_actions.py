"""Message context actions and their live Redstoner workspace."""

import asyncio
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_ui_discord as sd
from squid.bot.admin import Admin
from squid.bot.give_redstoner import RedstonerScreen
from squid_ui.testing import RecordingResponder, labels, press, press_event
from squid_ui_discord.testing import AsyncCallRecorder


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

    async def __call__(self) -> bool:
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
            can_deploy=can_deploy,
            authorize_deploy=authorizer,
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


@pytest.mark.parametrize("outcome", ["success", "send_failure", "hidden", "same_channel", "other_user", "other_guild"])
async def test_paste_moves_only_the_callers_accessible_selection(outcome: str, monkeypatch: pytest.MonkeyPatch) -> None:
    authorize = AsyncMock()
    monkeypatch.setattr("squid.bot.admin.enforce", authorize)
    source = Mock(spec=discord.TextChannel)
    source.permissions_for.return_value = SimpleNamespace(view_channel=outcome != "hidden", read_message_history=True)
    current = Mock(spec=discord.Message)
    source.fetch_message = AsyncMock(return_value=current)
    selected = SimpleNamespace(channel=source, id=123)
    guild = SimpleNamespace(id=2 if outcome == "other_guild" else 1, fetch_member=AsyncMock())
    destination = source if outcome == "same_channel" else Mock(spec=discord.TextChannel)
    request = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=8 if outcome == "other_user" else 7),
        channel=destination,
        interaction=Mock(spec=discord.Interaction),
    )
    move = AsyncMock(side_effect=RuntimeError("send failed") if outcome == "send_failure" else None)
    owner = SimpleNamespace(_pending_archives={(1, 7): selected}, _paste_lock=asyncio.Lock(), _archive_message=move)
    callback = inspect.unwrap(cast(Any, Admin).paste.callback)
    if outcome == "send_failure":
        with pytest.raises(RuntimeError, match="send failed"):
            await callback(owner, request)
    else:
        await callback(owner, request)
    authorize.assert_awaited_once()
    if outcome == "success":
        move.assert_awaited_once_with(current, request.interaction)
        assert owner._pending_archives == {}
    else:
        assert owner._pending_archives == {(1, 7): selected}
        if outcome != "send_failure":
            move.assert_not_awaited()


async def test_archive_selection_does_not_move_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("squid.bot.admin.enforce", AsyncMock())
    guild = SimpleNamespace(id=1)
    message = SimpleNamespace(guild=guild, delete=AsyncMock())
    request = SimpleNamespace(guild=guild, user=SimpleNamespace(id=7))
    owner = SimpleNamespace(_pending_archives={})
    await Admin.archive_message_context(cast(Any, owner), cast(Any, request), cast(discord.Message, message))
    assert owner._pending_archives == {(1, 7): message}
    message.delete.assert_not_awaited()
