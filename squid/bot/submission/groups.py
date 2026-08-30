"""Shared command groups for submission cogs."""

from typing import TYPE_CHECKING

from discord import app_commands

from squid_ui_discord.ext import Cog

if TYPE_CHECKING:
    import squid.bot.app


class BuildCommandGroup[BotT: "squid.bot.app.RedstoneSquid"](Cog[BotT]):
    """Own the app-only build command group."""

    build_group = app_commands.Group(name="build", description="Browse and submit redstone builds")
