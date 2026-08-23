"""Various admin commands for the bot."""

import re
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Greedy

from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import resolve_locale, t
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.ui import info_layout, link_layout, reply_presentation, text_layout
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import hide_unless, requires
from squid.bot.utils.visibility import personal
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    MESSAGE_ARCHIVE_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid.tags.domain import TagValueType

if TYPE_CHECKING:
    import squid.bot.app


class Admin[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.tags = bot.services.tags
        self._archive_header_pattern = re.compile(r"^<@!?(\d+)>.*wrote:")
        self.bot.reactions.subscribe(self)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)

    @commands.hybrid_group(name="tag")
    async def tag_group(self, ctx: Context[BotT]) -> None:
        """Propose, apply, and review build tags."""
        await ctx.send_help("tag")

    @autocompletes(query_name="search_fields")
    @tag_group.command(name="propose")
    @app_commands.describe(
        name=app_commands.locale_str(_("Public display name.")),
        value_type=app_commands.locale_str(_("Whether the tag carries a number, text, yes/no value, or no value.")),
        query_name=app_commands.locale_str(_("Optional search and sort field, for example closing_delay.")),
    )
    async def propose_tag(
        self,
        ctx: Context[BotT],
        name: str,
        value_type: TagValueType = TagValueType.NONE,
        query_name: str | None = None,
    ) -> None:
        """Propose a build tag for staff review."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        account_id = await ensure_consented_account(ctx, self.bot.services.accounts, locale=locale)
        if account_id is None:
            return
        definition = await self.tags.propose_showcase(
            name,
            value_type=value_type,
            query_name=query_name,
            created_by_account_id=account_id,
        )
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Tag proposed")),
                t(locale, _("Tag #{id} is awaiting staff approval."), id=definition.id),
            ),
            visibility="personal" if personal(ctx) else "public",
        )

    @autocompletes(build_id="builds", tag_id="showcase_tag_ids")
    @tag_group.command(name="apply")
    @app_commands.describe(
        build_id=app_commands.locale_str(_("A build you submitted.")),
        tag_id=app_commands.locale_str(_("An approved build tag.")),
        value=app_commands.locale_str(_("The tag value, omitted for plain tags.")),
    )
    async def tag_build(
        self,
        ctx: Context[BotT],
        build_id: int,
        tag_id: int,
        value: str | None = None,
    ) -> None:
        """Apply an approved tag to one of your builds."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        account_id = await ensure_consented_account(ctx, self.bot.services.accounts, locale=locale)
        if account_id is None:
            return
        tag = await self.tags.assign_showcase(
            build_id,
            tag_id,
            value,
            actor_account_id=account_id,
        )
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Build tagged")),
                t(locale, _("Attached **{name}** to build #{id}."), name=tag.display_name, id=build_id),
            ),
            visibility="personal" if personal(ctx) else "public",
        )

    @tag_group.command(name="pending")
    @requires(TAG_PROPOSAL_LIST)
    async def pending_tags(self, ctx: Context[BotT]) -> None:
        """List user tags awaiting moderation."""
        definitions = await self.tags.pending()
        body = "\n".join(
            f"**#{tag.id}** {tag.display_name} ({tag.value_type.value}; `{tag.query_name or 'no field'}`)"
            for tag in definitions
        )
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Pending tags")),
                body or t(locale, _("No tags are awaiting review.")),
            ),
            visibility="personal" if personal(ctx) else "public",
        )

    @autocompletes(tag_id="tags_pending")
    @tag_group.command(name="approve")
    @requires(TAG_PROPOSAL_APPROVE)
    async def approve_tag(self, ctx: Context[BotT], tag_id: int) -> None:
        """Publish a proposed showcase tag."""
        tag = await self.tags.approve(tag_id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Tag approved")),
                t(locale, _("Published **{name}**."), name=tag.display_name),
            ),
        )

    @autocompletes(tag_id="tags_pending")
    @tag_group.command(name="reject")
    @requires(TAG_PROPOSAL_REJECT)
    async def reject_tag(self, ctx: Context[BotT], tag_id: int) -> None:
        """Reject a proposed showcase tag."""
        tag = await self.tags.reject(tag_id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Tag rejected")),
                t(locale, _("Rejected **{name}**."), name=tag.display_name),
            ),
        )

    @autocompletes(tag_id="showcase_tag_ids")
    @tag_group.command(name="archive")
    @requires(TAG_PROPOSAL_ARCHIVE)
    async def archive_tag(self, ctx: Context[BotT], tag_id: int) -> None:
        """Archive a published tag."""
        tag = await self.tags.archive(tag_id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            info_layout(
                t(locale, _("Tag archived")),
                t(locale, _("Archived **{name}**."), name=tag.display_name),
            ),
        )

    @commands.hybrid_command(name="archive")
    @requires(MESSAGE_ARCHIVE_CREATE, guild_only=True)
    @hide_unless(manage_messages=True)
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

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        payload = event.payload
        if event.emoji != "❌":
            return
        assert self.bot.user is not None
        if payload.user_id == self.bot.user.id:
            return
        message = await event.message()
        if message is None:
            return
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

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """Ignore removals from archived messages."""

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Ignore clears from archived messages."""

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        """Ignore emoji clears from archived messages."""

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

            locale = await resolve_locale(ctx, self.bot.services.settings)
            scope = t(locale, _("globally")) if spec is None else t(locale, _("to the current guild"))
            await reply_presentation(
                ctx,
                text_layout(t(locale, _("Synced {count} commands {scope}."), count=len(synced), scope=scope)),
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

        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            text_layout(t(locale, _("Synced the tree to {synced}/{total}."), synced=ret, total=len(guilds))),
        )

    @commands.command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, ctx: Context[BotT]):
        """Sends the google sheets link"""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            link_layout(
                t(locale, _("Build spreadsheet")),
                "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221",
                label=t(locale, _("Open spreadsheet")),
            ),
        )

    @commands.command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context[BotT]):
        """Sends the database link"""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_presentation(
            ctx,
            link_layout(
                t(locale, _("Database")),
                "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc",
                label=t(locale, _("Open database")),
            ),
        )

    # Not `error`: that name now belongs to the stored-error lookup group, which is the command
    # someone reaches for while holding a reference a user reported.
    @commands.command(name="raise-error", aliases=["e"], hidden=True)
    @commands.is_owner()
    async def raise_error(self, ctx: Context[BotT]):
        """Raises an error for testing purposes."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, delete_on_exit=True, locale=locale):
            msg = "This is a test error."
            raise ValueError(msg)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
