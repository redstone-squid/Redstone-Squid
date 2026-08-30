"""The one ephemerality rule, at the sites where getting it wrong costs something (audit C2)."""

from dataclasses import dataclass
from typing import Any, cast

import discord
import pytest
from discord.ext.commands import Context

import squid_ui_discord as sd
from squid.bot.ui import text_node
from squid.settings.application import SettingsService
from squid_ui_discord.testing import AsyncCallRecorder, ContextHarness, InteractionHarness, MessageHarness
from tests.support.discord import make_layout_bot


@dataclass(frozen=True)
class Guild:
    id: int
    preferred_locale: str


@dataclass(frozen=True)
class HttpResponse:
    status: int
    reason: str


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


@dataclass(frozen=True)
class Services:
    settings: SettingsService


def make_context(bot: Any, *, slash: bool = False, in_guild: bool = True, dm_raises: Exception | None = None) -> Any:
    context = ContextHarness(message=MessageHarness(), bot=bot, user_id=1)
    context.guild = Guild(5, "en-US") if in_guild else None
    context.author.send = AsyncCallRecorder(result=MessageHarness(guild_id=None), error=dm_raises)
    if slash:
        interaction = InteractionHarness(user_id=1).source
        interaction.guild_locale = None
        interaction.locale = "en-US"
        interaction.expires_at = None
        context.interaction = interaction
    return context.source


def make_bot() -> Any:
    return make_layout_bot(services=Services(settings=SettingsRecorder()))


def _rendered(call: Any) -> str:
    return str(call.kwargs["view"].to_components())


@pytest.mark.parametrize(("slash", "ephemeral"), [(True, True), (False, False)])
async def test_personal_visibility_matches_the_available_transport(slash: bool, ephemeral: bool) -> None:
    bot = make_bot()
    ctx = make_context(bot, slash=slash)

    await bot.app_ui.respond(cast(Context[Any], ctx), text_node("personal"), audience="personal")

    assert ctx.send.await_args.kwargs["ephemeral"] is ephemeral


async def test_a_closed_dm_delivers_nothing_rather_than_falling_back() -> None:
    """The channel is exactly what the payload must not reach, so there is nowhere to fall back to."""
    bot = make_bot()
    ctx = make_context(bot, dm_raises=discord.Forbidden(cast(Any, HttpResponse(status=403, reason="")), "no dms"))
    with pytest.raises(sd.delivery.DeliveryAbandoned):
        await bot.app_ui.respond(
            cast(Context[Any], ctx),
            text_node("secret"),
            audience=sd.Private("Because it is a credential."),
        )

    assert "secret" not in _rendered(ctx.send.await_args)
    assert "direct message" in _rendered(ctx.send.await_args)


async def test_a_direct_message_context_answers_where_it_was_asked() -> None:
    """A DM is already private, so routing it to another DM would just be a second message."""
    bot = make_bot()
    ctx = make_context(bot, in_guild=False)
    await bot.app_ui.respond(
        cast(Context[Any], ctx),
        text_node("secret"),
        audience=sd.Private("Because it is a credential."),
    )

    ctx.author.send.assert_not_awaited()
    assert "secret" in _rendered(ctx.send.await_args)
