"""Various admin commands for the bot."""

from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Greedy
from rapidfuzz import process

from squid.bot import utils
from squid.bot.utils import check_is_owner_server, check_is_staff
from squid.db.build_tags import (
    AliasAlreadyAdded,
    AliasTakenByOther,
    RestrictionNotFound,
)
from squid.db.builds import Build

if TYPE_CHECKING:
    import squid.bot


class Admin[BotT: "squid.bot.RedstoneSquid"](commands.Cog):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        self.bot = bot

    @commands.hybrid_command(name="confirm")
    @app_commands.describe(build_id="The ID of the build you want to confirm.")
    @check_is_staff()
    @check_is_owner_server()
    async def confirm_build(self, ctx: Context[BotT], build_id: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await Build.from_id(build_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending build with that ID.")
                await sent_message.edit(embed=error_embed)
                return

            await build.confirm()
            self.bot.dispatch("build_confirmed", build)

            success_embed = utils.info_embed("Success", "Submission has been confirmed.")
            await sent_message.edit(embed=success_embed)

    @commands.hybrid_command(name="deny")
    @app_commands.describe(build_id="The ID of the build you want to deny.")
    @check_is_staff()
    @check_is_owner_server()
    async def deny_build(self, ctx: Context[BotT], build_id: int):
        """Marks a submission as denied."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await Build.from_id(build_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending submission with that ID.")
                await sent_message.edit(embed=error_embed)
                return

            await build.deny()
            await self.bot.for_build(build).update_messages()

            success_embed = utils.info_embed("Success", "Submission has been denied.")
            await sent_message.edit(embed=success_embed)

    @commands.hybrid_command("add_alias")
    @check_is_staff()
    @check_is_owner_server()
    async def add_restriction_alias(self, ctx: Context[BotT], restriction: str, alias: str):
        """Add an alias for a restriction."""
        async with self.bot.get_running_message(ctx) as sent_message:
            try:
                await self.bot.db.build_tags.add_restriction_alias(restriction, alias)
            except RestrictionNotFound:
                await sent_message.edit(embed=utils.error_embed("Error", f"No restriction named '{restriction}'."))
            except AliasAlreadyAdded:
                await sent_message.edit(embed=utils.info_embed("Already added", "Alias already on this restriction."))
            except AliasTakenByOther as e:
                await sent_message.edit(
                    embed=utils.error_embed(
                        "Error",
                        f"Alias in use by another restriction (ID: {e.other_id}).",
                    )
                )
            else:
                await sent_message.edit(embed=utils.info_embed("Success", "Alias added."))

    @add_restriction_alias.autocomplete("restriction")
    async def restriction_autocomplete(
        self, _interaction: discord.Interaction[BotT], current: str
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete for restriction names."""
        if not current:
            return []

        restrictions = await self.bot.db.build_tags.fetch_all_restrictions()
        restriction_names = [r.name for r in restrictions]
        matches = process.extract(
            current,
            restriction_names,
            limit=25,
            score_cutoff=30,
        )
        return [app_commands.Choice(name=match[0], value=match[0]) for match in matches]

    @commands.hybrid_command(name="archive")
    @check_is_staff()
    async def archive_message(self, ctx: Context[BotT], message: discord.Message, delete_original: bool = True):
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
    @commands.is_owner()
    async def sync(self, ctx: Context[BotT], guilds: Greedy[discord.Object], spec: Literal["~", "*", "^"] | None = None) -> None:  # fmt: skip
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
    async def get_sheets_link(self, ctx: Context[BotT]):
        """Sends the google sheets link"""
        await ctx.send(
            "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221"
        )

    @commands.command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context[BotT]):
        """Sends the database link"""
        await ctx.send(
            "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc"
        )

    @commands.command(name="error", aliases=["e"], hidden=True)
    @commands.is_owner()
    async def error(self, ctx: Context[BotT]):
        """Raises an error for testing purposes."""
        async with self.bot.get_running_message(ctx, delete_on_exit=True):
            raise ValueError("This is a test error.")


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
