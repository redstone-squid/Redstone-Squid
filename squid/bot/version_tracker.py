"""A cog to manage new minecraft versions"""

from typing import TYPE_CHECKING


import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog
from squid.db import DatabaseManager, ServerSettingManager
from squid.db.utils import parse_version_string

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


class VersionTracker[BotT: RedstoneSquid](Cog, name="VersionTracker"):
    def __init__(self, bot: BotT):
        self.bot = bot

    @commands.hybrid_command()
    async def add_version(self, ctx: commands.Context, edition: str, version_string: str):
        db = DatabaseManager()

        edition, major_version, minor_version, patch = parse_version_string(edition + version_string)

        version_record = {
            "edition": edition,
            "major_version": major_version,
            "minor_version": minor_version,
            "patch_number": patch,
        }

        response = await db.table("versions").insert(version_record).execute()
        await ctx.send(f"Version added successfully: {response.data}")

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message):
        if message.channel.id not in [
            ServerSettingManager.get_single(server_id=message.guild.id, setting="JavaChangelog"),
            ServerSettingManager.get_single(server_id=message.guild.id, setting="BedrockChangelog"),
        ]:
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

        response = await db.table("versions").insert(version_record).execute()
        await message.channel.send(f"Version added successfully: {response.data}")


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VersionTracker(bot))
