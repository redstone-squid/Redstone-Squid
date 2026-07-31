"""A cog to manage new minecraft versions"""

from typing import TYPE_CHECKING, Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog, Context, hybrid_group
from discord.ext.commands.bot import app_commands

from squid.bot.utils.components import info_layout, no_mentions, text_layout
from squid.bot.utils.permissions import check_is_owner_server, check_is_staff

if TYPE_CHECKING:
    import squid.bot.app


class VersionTracker[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="VersionTracker"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.version_service = bot.services.versions

    @hybrid_group(name="version")
    async def version_group(self, ctx: Context[BotT]) -> None:
        """List and manage recognized Minecraft versions."""
        await ctx.send_help("version")

    @version_group.command(name="list")
    async def versions(self, ctx: Context[BotT]):
        """List the Minecraft versions the bot recognizes."""
        versions_human_readable = await self.version_service.list_display("Java", limit=20)  # TODO: pagination
        await ctx.send(
            view=info_layout("Recognized Java versions", ", ".join(versions_human_readable)),
            allowed_mentions=no_mentions(),
        )

    @version_group.command(name="add")
    @check_is_staff()
    @check_is_owner_server()
    @app_commands.rename(version_string="version")
    async def add_version(self, ctx: commands.Context, edition: Literal["Java", "Bedrock"], version_string: str):
        """Add a Minecraft version to the database."""
        version = await self.version_service.add(version_string, edition=edition)
        await ctx.send(
            view=text_layout(f"Version added successfully: {version}"),
            allowed_mentions=no_mentions(),
        )

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message):
        """Parse messages in the version-tracking channel and add them to the database"""
        minecraft_version_tracker_channel = 1334168723170263122

        channel_id = message.channel.id
        if channel_id != minecraft_version_tracker_channel:
            return

        first_line = message.content.split("\n", 1)[0]
        version = await self.version_service.add(first_line)
        await self.bot.get_channel(channel_id).send(  # type: ignore
            view=text_layout(f"Version added successfully: {version}"),
            allowed_mentions=no_mentions(),
        )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VersionTracker(bot))
