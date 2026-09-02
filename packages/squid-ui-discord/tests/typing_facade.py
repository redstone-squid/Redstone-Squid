"""Pyrefly fixture pinning owner, request, and outcome inference."""

from typing import Any, Self, assert_type

import discord
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd


class Panel(sd.Screen[object]):
    def render(self) -> sl.LayoutNode:
        return sl.paragraph("Panel")


class Builds(sd.Cog[commands.Bot]):
    tools = sd.Group(name="tools", description="Tools", defer="private")

    @sd.command(defer="private", description="Show a build")
    async def build(self, request: sd.Request[Self], build_id: int) -> Panel:
        assert_type(request.owner, Self)
        assert_type(request.interaction, discord.Interaction[Any] | None)
        return Panel()

    @tools.command(name="ping")
    async def ping(self, request: sd.Request[Self]) -> str:
        return "Pong"

    @sd.hybrid_command(aliases=["p"])
    async def prefix(self, request: sd.Request[Self]) -> str:
        return "Prefix"

    @sd.prefix_command(name="text-only", hidden=True)
    async def text_only(self, request: sd.Request[Self]) -> str:
        return "Text only"

    @sd.context_menu(name="Inspect", default_permissions=discord.Permissions(manage_messages=True))
    async def inspect(self, request: sd.Request[Self], target: discord.Message) -> None:
        await request.respond("Inspected")


def declarations_are_native(cog: Builds) -> None:
    build: app_commands.Command[Builds, ..., None] = cog.build
    prefix: commands.HybridCommand[Builds, ..., None] = cog.prefix
    text_only: commands.Command[Builds, ..., None] = cog.text_only
    assert_type(cog.tools, sd.Group)
    assert_type(build.binding, Builds | None)
    assert_type(prefix.cog, Builds)
    assert_type(text_only.cog, Builds)


@sd.command(unknown_future_kwarg=True)
async def free(request: sd.Request, value: int) -> str:
    assert_type(request.owner, Any)
    return str(value)


async def facade_inference(
    runtime: sd.DiscordUIRuntime[commands.Bot],
    cog: Builds,
    interaction: discord.Interaction[commands.Bot],
    event: sl.PressEvent,
) -> None:
    ui = runtime.scope(cog)
    assert_type(ui, sd.Scope[Builds])

    request = await ui.request(interaction)
    assert_type(request, sd.Request[Builds])
    assert_type(request.owner, Builds)

    outcome = await request.respond(Panel())
    assert_type(outcome, sd.ResponseResult[Panel])

    click = await sd.request(event)
    assert_type(click, sd.Request[Any])
    assert_type(click.root, sd.MessageRoot | None)

    runtime.respond(interaction, "wrong owner")  # pyrefly: ignore[missing-attribute]
