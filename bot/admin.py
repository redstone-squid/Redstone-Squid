"""Various admin commands for the bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
from discord.ext import commands
from discord.ext.commands import Context, Bot, Greedy

from bot.utils import check_is_staff, RunningMessage

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


class Admin(commands.Cog):
    """Cog for admin commands."""

    def __init__(self, bot: RedstoneSquid):
        self.bot = bot

    @commands.hybrid_command(name="archive")
    @check_is_staff()
    async def archive_message(self, ctx: Context[Bot], message: discord.Message, delete_original: bool = False):
        """Makes a copy of the message in the current channel."""
        if isinstance(message.author, discord.User):
            user = message.author
        else:
            user = self.bot.get_user(message.author.id)
        username_description = f" (username: {user.name})" if user else ""

        await ctx.send(
            content=f"{message.author.mention}{username_description} wrote:\n```\n{message.clean_content}```",
            embeds=message.embeds,
            files=[await attachment.to_file() for attachment in message.attachments],
            stickers=message.stickers,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if delete_original:
            await message.delete()

    @commands.command(name="s", hidden=True)
    @commands.guild_only()
    @commands.is_owner()
    async def sync(self, ctx: Context[Bot], guilds: Greedy[discord.Object], spec: Literal["~", "*", "^"] | None = None) -> None:  # fmt: skip
        """Syncs the slash commands with the discord API."""
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)  # type: ignore
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}")
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    @commands.command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, ctx: Context):
        """Sends the google sheets link"""
        await ctx.send(
            "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221"
        )

    @commands.command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context):
        """Sends the database link"""
        await ctx.send(
            "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc"
        )

    @commands.command(name="error", aliases=["e"], hidden=True)
    @commands.is_owner()
    async def error(self, ctx: Context):
        """Raises an error for testing purposes."""
        async with RunningMessage(ctx, delete_on_exit=True):
            raise ValueError("This is a test error.")


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
