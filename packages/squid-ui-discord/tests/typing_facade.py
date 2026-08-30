"""Pyrefly fixture pinning owner, source, action, and outcome inference."""

from typing import Self, assert_type

import discord
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_discord.ext as sdx


class Panel(sd.Screen[object]):
    def render(self) -> sl.LayoutNode:
        return sl.paragraph("Panel")


class Builds(sdx.Cog[commands.Bot]):
    @app_commands.command()
    @sdx.command()
    async def build(self, request: sdx.DiscordRequest[Self], build_id: int) -> Panel:
        assert_type(request.owner, Self)
        assert_type(request.source, sd.ResponseSource)
        return Panel()


async def facade_inference(
    runtime: sd.DiscordUIRuntime[commands.Bot],
    cog: Builds,
    interaction: discord.Interaction[commands.Bot],
    event: sl.PressEvent,
) -> None:
    ui = runtime.scope(cog)
    assert_type(ui, sd.DiscordUI[Builds])

    request = await ui.resolve(interaction)
    assert_type(request, sd.DiscordRequest[Builds, discord.Interaction[commands.Bot]])

    outcome = await ui.respond(interaction, Panel())
    assert_type(outcome, sd.ResponseResult[Panel])

    action = ui.action(event)
    assert_type(action, sd.DiscordAction[sl.PressEvent, Builds])

    runtime.respond(interaction, "wrong owner")  # pyrefly: ignore[missing-attribute]
    cog.bot.ui.respond(interaction, "wrong owner")  # pyrefly: ignore[missing-attribute]
