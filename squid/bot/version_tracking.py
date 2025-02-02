"""A cog to manage new minecraft versions"""

from typing import Literal

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog
from postgrest.base_request_builder import APIResponse

from squid.db import DatabaseManager
from squid.db.schema import VersionRecord
from squid.db.utils import parse_version_string
from squid.bot import RedstoneSquid


class VersionTracker[BotT: RedstoneSquid](Cog, name="VersionTracker"):
    def __init__(self, bot: BotT):
        self.bot = bot

    @commands.hybrid_command()
    async def add_version(self, ctx: commands.Context, edition: Literal["Java", "Bedrock"], version_string: str):
        """Add a new version to the database"""
        db = DatabaseManager()
        edition, major_version, minor_version, patch = parse_version_string(edition + version_string)
        version_record = {
            "edition": edition,
            "major_version": major_version,
            "minor_version": minor_version,
            "patch_number": patch,
        }
        response: APIResponse[VersionRecord] = await db.table("versions").insert(version_record).execute()
        await ctx.send(f"Version added successfully: {response.data}")

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message):
        """Parse messages in the version-tracking channel and add them to the database"""
        channel_id = message.channel.id
        if channel_id != 1334168723170263122:
            return

        first_line = message.content.split("\n", 1)[0]
        edition, major_version, minor_version, patch = parse_version_string(first_line)
        db = DatabaseManager()

        version_record = {
            "edition": edition,
            "major_version": major_version,
            "minor_version": minor_version,
            "patch_number": patch,
        }

        response: APIResponse[VersionRecord] = await db.table("versions").insert(version_record).execute()
        await self.bot.get_channel(channel_id).send(f"Version added successfully: {response.data}")  # type: ignore


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VersionTracker(bot))
