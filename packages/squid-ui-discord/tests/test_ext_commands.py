"""Composable discord.py wrappers over owner-scoped facade requests."""

from typing import Any, Self, cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import squid_ui_discord as sd
import squid_ui_discord.ext as sdx
from squid_ui_discord.testing import InteractionHarness


def installed_bot() -> commands.Bot:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cast(Any, bot).ui = sd.install(bot)
    return bot


def interaction_for(bot: commands.Bot, *, user_id: int = 7) -> InteractionHarness:
    interaction = InteractionHarness(user_id=user_id)
    cast(Any, interaction).client = bot
    return interaction


class Builds(sdx.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.request: sdx.DiscordRequest[Self] | None = None

    @app_commands.command()
    @sdx.command(acknowledgement="private")
    async def build(self, request: sdx.DiscordRequest[Self], build_id: int) -> str:
        self.request = request
        return f"Build {build_id}"

    @commands.command()
    @sdx.command()
    async def prefix(self, request: sdx.DiscordRequest[Self]) -> str:
        self.request = request
        return "Prefix"

    @sdx.autocomplete()
    async def choices(
        self,
        request: sdx.DiscordRequest[Self],
        current: str,
    ) -> list[tuple[str, str]]:
        assert request.owner is self
        return [(f"{current} {index}", str(index)) for index in range(30)]


async def test_application_command_injects_owner_request_and_presents_return() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    await cast(Any, type(cog).build).callback(cog, interaction.source, 42)

    assert cog.request is not None
    assert cog.request.owner is cog
    assert cog.request.source is interaction.source
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    interaction.edit_original_response.assert_awaited_once()


async def test_prefix_command_keeps_a_native_outward_callback() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    assert isinstance(type(cog).prefix, commands.Command)
    await cast(Any, type(cog).prefix.callback)(cog, interaction.source)

    assert cog.request is not None
    assert cog.request.owner is cog
    interaction.response.send_message.assert_awaited_once()


async def test_autocomplete_normalizes_and_limits_choices() -> None:
    bot = installed_bot()
    cog = Builds(bot)
    interaction = interaction_for(bot)

    choices = await cog.choices(interaction.source, "redstone")

    assert len(choices) == 25
    assert choices[0] == app_commands.Choice(name="redstone 0", value="0")


class Menus(sdx.Cog[commands.Bot]):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.target: object | None = None

    @sdx.context_menu(name="Inspect build", acknowledgement="public")
    async def inspect(
        self,
        request: sdx.DiscordRequest[Self],
        target: discord.Message,
    ) -> str:
        assert request.owner is self
        self.target = target
        return "Inspected"


async def test_context_menu_is_registered_invoked_and_removed_with_cog() -> None:
    bot = installed_bot()
    cog = Menus(bot)
    interaction = interaction_for(bot)
    target = object()

    await bot.add_cog(cog)
    menu = bot.tree.get_command("Inspect build", type=discord.AppCommandType.message)
    assert isinstance(menu, app_commands.ContextMenu)
    await menu._invoke(interaction.source, target)

    assert cog.target is target
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": False, "thinking": True}
    await bot.remove_cog(cog.qualified_name)
    assert cog.ui.closed
    assert bot.tree.get_command("Inspect build", type=discord.AppCommandType.message) is None


async def test_failed_load_rolls_back_menu_and_scope() -> None:
    class Broken(Menus):
        async def ui_load(self) -> None:
            raise RuntimeError("broken")

    bot = installed_bot()
    cog = Broken(bot)

    with pytest.raises(RuntimeError, match="broken"):
        await bot.add_cog(cog)

    assert cog.ui.closed
    assert bot.tree.get_command("Inspect build", type=discord.AppCommandType.message) is None


def test_native_lifecycle_hooks_cannot_bypass_scope_cleanup() -> None:
    with pytest.raises(TypeError, match="ui_load/ui_unload"):

        class Unsafe(sdx.Cog[commands.Bot]):
            async def cog_unload(self) -> None:
                pass


def test_request_must_occupy_the_native_source_slot() -> None:
    with pytest.raises(TypeError, match="native source slot"):

        @cast(Any, sdx.command())
        async def misplaced(owner: object, value: int, request: sdx.DiscordRequest[object]) -> None:
            pass
