"""Message context actions and their live Redstoner workspace."""

from dataclasses import dataclass, field
from typing import Any, cast

import discord

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
    count: int


@dataclass
class Message:
    author: Author
    reactions: tuple[Reaction, ...]
    channel: Channel
    clean_content: str
    embeds: tuple[str, ...]
    attachments: tuple[Attachment, ...]
    stickers: tuple[str, ...]
    delete: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass(frozen=True)
class User:
    name: str


class Bot:
    def get_user(self, user_id: int) -> User:
        assert user_id == 7
        return User("builder")


async def test_archive_copies_attachments_and_deletes_the_original() -> None:
    archived = ArchivedMessage()
    channel = Channel(send=AsyncCallRecorder(result=archived))
    author = Author(id=7, mention="<@7>")
    attachment = Attachment()
    message = Message(
        author=author,
        reactions=(Reaction(count=3), Reaction(count=2)),
        channel=channel,
        clean_content="A useful old message",
        embeds=("embed",),
        attachments=(attachment,),
        stickers=("sticker",),
    )
    cog = Admin.__new__(Admin)
    cog.bot = cast(Any, Bot())

    await cog._archive_message(cast(discord.Message, message))

    attachment.to_file.assert_awaited_once_with()
    call = channel.send.await_args
    assert call is not None
    assert "Reactions: 5" in call.kwargs["content"]
    assert "A useful old message" in call.kwargs["content"]
    assert call.kwargs["files"] == ["upload"]
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

    assert screen.session is not None
    assert screen.session.scope is sd.ScopeKind.USER_GUILD
    assert screen.audience == "personal"
    assert screen.timeout == 300
    assert {"Deploy role controls", "Close"} <= set(labels(screen.render()))


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
