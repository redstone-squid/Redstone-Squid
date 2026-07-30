"""A cog to manage new minecraft versions"""

from typing import TYPE_CHECKING, Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog
from discord.ext.commands.bot import app_commands

from squid.bot.utils.permissions import check_is_owner_server, check_is_staff

if TYPE_CHECKING:
    import squid.bot.app


class VersionTracker[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="VersionTracker"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.version_service = bot.services.versions

    @commands.hybrid_command()
    @check_is_staff()
    @check_is_owner_server()
    @app_commands.rename(version_string="version")
    async def add_version(self, ctx: commands.Context, edition: Literal["Java", "Bedrock"], version_string: str):
        """Add a new version to the database"""
        version = await self.version_service.add(version_string, edition=edition)
        await ctx.send(f"Version added successfully: {version}")

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message):
        """Parse messages in the version-tracking channel and add them to the database"""
        minecraft_version_tracker_channel = 1334168723170263122

        channel_id = message.channel.id
        if channel_id != minecraft_version_tracker_channel:
            return

        first_line = message.content.split("\n", 1)[0]
        version = await self.version_service.add(first_line)
        await self.bot.get_channel(channel_id).send(f"Version added successfully: {version}")  # type: ignore


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VersionTracker(bot))
