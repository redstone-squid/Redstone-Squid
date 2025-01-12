from __future__ import annotations

from typing import Literal, TYPE_CHECKING

import discord
import discord.ext.commands as commands
from discord import Member
from discord.ext import tasks
from discord.ext.commands import Context, Cog, Greedy, Bot
from discord.utils import escape_markdown

import bot.utils as utils
from bot.config import SOURCE_CODE_URL, BOT_NAME, FORM_LINK
from bot.utils import check_is_staff, RunningMessage
from database import DatabaseManager, get_version_string

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


class Miscellaneous(Cog):
    def __init__(self, bot: RedstoneSquid):
        self.bot: RedstoneSquid = bot

    @commands.hybrid_command()
    async def invite_link(self, ctx: Context):
        """Invite me to your other servers!"""
        await ctx.send(
            f"https://discordapp.com/oauth2/authorize?client_id={str(ctx.bot.user.id)}&scope=bot&permissions=8"
        )

    # Docstring can't be an f-string, so we use the help parameter instead.
    @commands.hybrid_command(help=f"Link to {BOT_NAME}'s source code.")
    async def source_code(self, ctx: Context):
        """Send a link to the source code."""
        await ctx.send(f"Source code can be found at: {SOURCE_CODE_URL}.")

    @commands.hybrid_command()
    async def google_forms(self, ctx: Context):
        """Links you to our record submission form. You want to use /submit instead."""
        em = discord.Embed(
            title="Submission form.",
            description=f"You can submit new records with ease via our google form: {FORM_LINK}",
            colour=utils.discord_green,
        )
        await ctx.send(embed=em)

    @commands.hybrid_command()
    async def docs(self, ctx: Context):
        """Links you to our regulations."""
        await ctx.send("https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit")

    @commands.hybrid_command(name="versions")
    async def versions(self, ctx: Context):
        """Shows a list of versions the bot recognizes."""
        versions = await DatabaseManager.fetch_versions_list(edition="Java")
        versions_human_readable = [get_version_string(version) for version in versions[:20]]  # TODO: pagination
        await ctx.send(", ".join(versions_human_readable))

    @commands.hybrid_command(name="archive")
    @check_is_staff()
    async def archive_message(self, ctx: Context, message: discord.Message, delete_original: bool = False):
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

    @tasks.loop(hours=24)
    async def call_supabase_to_prevent_deactivation(self):
        """Supabase deactivates a database in the free tier if it's not used for 7 days."""
        db = DatabaseManager()
        await db.table("submissions").select("submission_id").limit(1).execute()

    # ----------------- Owner only commands -----------------
    # These commands are only available to the bot owner.
    # I use them for debugging and testing purposes.
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

    @staticmethod
    async def is_my_alt(ctx: Context):
        return ctx.author.id == 1146802450100138004

    @commands.command(name="r", hidden=True)
    @commands.check(is_my_alt)
    async def give_redstoner(self, ctx: Context):
        """Give redstoner role to my alt for testing. Does nothing for others."""
        if ctx.guild is None:
            raise ValueError("DM not supported")

        my_alt: Member = ctx.author  # type: ignore

        redstoner_role = ctx.guild.get_role(433670432420397060)
        if not redstoner_role:
            await ctx.send("Redstoner role not found.")
            return

        if redstoner_role in my_alt.roles:
            await my_alt.remove_roles(redstoner_role)
        else:
            await my_alt.add_roles(redstoner_role)

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
        async with utils.RunningMessage(ctx, delete_on_exit=True):
            raise ValueError("This is a test error.")


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Miscellaneous(bot))
