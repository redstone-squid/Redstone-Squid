"""Shared command groups for submission cogs."""

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext.commands import Cog

if TYPE_CHECKING:
    import squid.bot.app


class BuildCommandGroup[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Own the app-only build command group."""

    build_group = app_commands.Group(name="build", description="Browse and submit redstone builds")
