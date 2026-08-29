"""Message context actions and their live Redstoner workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.admin import Admin
from squid.bot.give_redstoner import RedstonerScreen
from squid_ui.testing import labels


async def test_archive_copies_attachments_and_deletes_the_original() -> None:
    archived = SimpleNamespace(add_reaction=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=archived))
    author = SimpleNamespace(id=7, mention="<@7>")
    attachment = SimpleNamespace(to_file=AsyncMock(return_value="upload"))
    message = SimpleNamespace(
        author=author,
        reactions=(SimpleNamespace(count=3), SimpleNamespace(count=2)),
        channel=channel,
        clean_content="A useful old message",
        embeds=("embed",),
        attachments=(attachment,),
        stickers=("sticker",),
        delete=AsyncMock(),
    )
    cog = Admin.__new__(Admin)
    cog.bot = cast(Any, SimpleNamespace(get_user=MagicMock(return_value=SimpleNamespace(name="builder"))))

    await cog._archive_message(cast(discord.Message, message))

    attachment.to_file.assert_awaited_once_with()
    call = channel.send.await_args
    assert call is not None
    assert "Reactions: 5" in call.kwargs["content"]
    assert "A useful old message" in call.kwargs["content"]
    assert call.kwargs["files"] == ["upload"]
    archived.add_reaction.assert_awaited_once_with("❌")
    message.delete.assert_awaited_once_with()


def make_redstoner_screen(
    *,
    can_deploy: bool = True,
    authorize: AsyncMock | None = None,
    publish: AsyncMock | None = None,
) -> tuple[RedstonerScreen, AsyncMock, AsyncMock]:
    authorizer = authorize or AsyncMock(return_value=True)
    publisher = publish or AsyncMock()
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

    assert screen.scope is sd.ScopeKind.USER_GUILD
    assert screen.visibility == "personal"
    assert screen.timeout == 300
    assert {"Deploy role controls", "Close"} <= set(labels(screen.render()))


def test_redstoner_hides_deployment_without_initial_permission() -> None:
    screen, _, _ = make_redstoner_screen(can_deploy=False)

    assert "Deploy role controls" not in labels(screen.render())


async def test_redstoner_rechecks_permission_before_deployment() -> None:
    authorize = AsyncMock(return_value=False)
    screen, _, publish = make_redstoner_screen(authorize=authorize)
    event = SimpleNamespace(notice=AsyncMock())

    await screen._deploy(cast(sl.PressEvent, event))

    authorize.assert_awaited_once_with()
    publish.assert_not_awaited()
    event.notice.assert_awaited_once()


async def test_redstoner_deploys_after_permission_recheck() -> None:
    screen, authorize, publish = make_redstoner_screen()
    event = SimpleNamespace(notice=AsyncMock())

    await screen._deploy(cast(sl.PressEvent, event))

    authorize.assert_awaited_once_with()
    publish.assert_awaited_once_with()
    event.notice.assert_awaited_once()
