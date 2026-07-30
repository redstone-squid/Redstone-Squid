"""Various admin commands for the bot."""

import re
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Greedy
from rapidfuzz import process

from squid.bot.utils.components import edit_layout, info_layout, link_layout, no_mentions, text_layout
from squid.bot.utils.permissions import check_is_owner_server, check_is_staff
from squid.builds.errors import AliasAlreadyAddedError

if TYPE_CHECKING:
    import squid.bot.app


class Admin[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.builds = bot.services.builds
        self.restrictions = bot.services.restrictions
        self._archive_header_pattern = re.compile(r"^<@!?(\d+)>.*wrote:")

    @commands.hybrid_command(name="confirm")
    @app_commands.describe(build_id="The ID of the build you want to confirm.")
    @check_is_staff()
    @check_is_owner_server()
    async def confirm_build(self, ctx: Context[BotT], build_id: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await self.builds.confirm(build_id)

            self.bot.dispatch("build_confirmed", build)

            await edit_layout(
                sent_message,
                info_layout("Success", "Submission has been confirmed."),
                allowed_mentions=no_mentions(),
            )

    @commands.hybrid_command(name="deny")
    @app_commands.describe(build_id="The ID of the build you want to deny.")
    @check_is_staff()
    @check_is_owner_server()
    async def deny_build(self, ctx: Context[BotT], build_id: int):
        """Marks a submission as denied."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await self.builds.deny(build_id)

            await self.bot.for_build(build).update_messages()

            await edit_layout(
                sent_message,
                info_layout("Success", "Submission has been denied."),
                allowed_mentions=no_mentions(),
            )

    @commands.hybrid_command("add_alias")
    @check_is_staff()
    @check_is_owner_server()
    async def add_restriction_alias(self, ctx: Context[BotT], restriction: str, alias: str):
        """Add an alias for a restriction."""
        async with self.bot.get_running_message(ctx) as sent_message:
            try:
                await self.restrictions.add_alias(restriction, alias)
            except AliasAlreadyAddedError:
                await edit_layout(
                    sent_message,
                    info_layout("Already added", "Alias already on this restriction."),
                    allowed_mentions=no_mentions(),
                )
            else:
                await edit_layout(
                    sent_message,
                    info_layout("Success", "Alias added."),
                    allowed_mentions=no_mentions(),
                )

    @add_restriction_alias.autocomplete("restriction")
    async def restriction_autocomplete(
        self, _interaction: discord.Interaction[BotT], current: str
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete for restriction names."""
        if not current:
            return []

        restriction_names = await self.restrictions.names()
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
        reaction_count = sum(reaction.count for reaction in message.reactions)

        sent_message = await ctx.send(
            content=(
                f"{message.author.mention}{username_description} wrote:"
                f"\nReactions: {reaction_count}"
                f"\n```\n{message.clean_content}```"
                "\nIf you are the author of this message, react with ❌ to delete this archived copy."
            ),
            embeds=message.embeds,
            files=[await attachment.to_file() for attachment in message.attachments],
            stickers=message.stickers,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=(message.author,), roles=False, replied_user=False
            ),
        )
        await sent_message.add_reaction("❌")
        if delete_original:
            await message.delete()

    @commands.Cog.listener(name="on_raw_reaction_add")
    async def remove_archived_message(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != "❌":
            return
        assert self.bot.user is not None
        if payload.user_id == self.bot.user.id:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        if not isinstance(channel, discord.abc.Messageable):
            return
        message = await channel.fetch_message(payload.message_id)
        if message.author.id != self.bot.user.id:
            return
        header = message.content.splitlines()[0] if message.content else ""
        match = self._archive_header_pattern.match(header)
        if not match:
            return
        author_id = int(match.group(1))
        if author_id != payload.user_id:
            return
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

            await ctx.send(
                view=text_layout(
                    f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
                ),
                allowed_mentions=no_mentions(),
            )
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(
            view=text_layout(f"Synced the tree to {ret}/{len(guilds)}."),
            allowed_mentions=no_mentions(),
        )

    @commands.command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, ctx: Context[BotT]):
        """Sends the google sheets link"""
        await ctx.send(
            view=link_layout(
                "Build spreadsheet",
                "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221",
                label="Open spreadsheet",
            ),
            allowed_mentions=no_mentions(),
        )

    @commands.command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context[BotT]):
        """Sends the database link"""
        await ctx.send(
            view=link_layout(
                "Database",
                "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc",
                label="Open database",
            ),
            allowed_mentions=no_mentions(),
        )

    @commands.command(name="error", aliases=["e"], hidden=True)
    @commands.is_owner()
    async def error(self, ctx: Context[BotT]):
        """Raises an error for testing purposes."""
        async with self.bot.get_running_message(ctx, delete_on_exit=True):
            msg = "This is a test error."
            raise ValueError(msg)


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
