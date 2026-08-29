"""Various admin commands for the bot."""

import re
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Greedy

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.i18n import resolve_locale, t
from squid.bot.operations import managed_result
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.tags_view import TagsScreen
from squid.bot.ui import link_node, text_node
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import allows, enforce
from squid.core.i18n import _
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    MESSAGE_ARCHIVE_CREATE,
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid_ui.document import DocumentLike

if TYPE_CHECKING:
    import squid.bot.app


class Admin[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.tags = bot.services.tags
        self.restrictions = bot.services.restrictions
        self._archive_header_pattern = re.compile(r"^<@!?(\d+)>.*wrote:")
        self.bot.reactions.subscribe(self)
        self.archive_ctx_menu = app_commands.ContextMenu(
            name="Archive Message",
            callback=self.archive_message_context,
        )
        self.archive_ctx_menu.default_permissions = discord.Permissions(manage_messages=True)
        self.bot.tree.add_command(self.archive_ctx_menu)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        self.bot.tree.remove_command(self.archive_ctx_menu.name, type=self.archive_ctx_menu.type)

    @autocompletes(build_id="builds")
    @app_commands.command(name="tags", description="Browse, apply, propose, and moderate build tags")
    @app_commands.rename(build_id="build")
    async def tags_workspace(self, interaction: discord.Interaction[BotT], build_id: int | None = None) -> None:
        """Open the capability-aware build tag workspace."""
        nodes = (
            TAG_PROPOSAL_LIST,
            TAG_PROPOSAL_APPROVE,
            TAG_PROPOSAL_REJECT,
            TAG_PROPOSAL_ARCHIVE,
            RESTRICTION_ALIAS_CREATE,
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        granted: set[PermissionNode] = set()
        for node in nodes:
            if await authorize(node):
                granted.add(node)
        capabilities = frozenset(granted)
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD,
            str(interaction.user.id),
        )
        account_id = (
            account.id if account is not None and account.id is not None and not account.needs_consent_refresh else None
        )
        await TagsScreen(
            self.tags,
            self.restrictions,
            build_id=build_id,
            actor_account_id=account_id,
            capabilities=capabilities,
            authorize=authorize,
        ).show(interaction)

    async def archive_message_context(
        self,
        interaction: discord.Interaction[BotT],
        message: discord.Message,
    ) -> None:
        """Archive the message selected through Discord's Apps menu."""
        await enforce(interaction, MESSAGE_ARCHIVE_CREATE)
        if interaction.guild is None or message.guild != interaction.guild:
            await interaction.response.send_message("That message is not from this server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._archive_message(message)
        await interaction.followup.send("Message archived.", ephemeral=True)

    async def _archive_message(self, message: discord.Message) -> None:
        """Copy one message in place and remove the original."""
        if isinstance(message.author, discord.User):
            user = message.author
        else:
            user = self.bot.get_user(message.author.id)
        username_description = f" (username: {user.name})" if user else ""
        reaction_count = sum(reaction.count for reaction in message.reactions)

        sent_message = await message.channel.send(
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
            invocation = await sd.Invocation.of(ctx)
            scope = t(locale, _("globally")) if spec is None else t(locale, _("to the current guild"))
            await invocation.reply(
                text_node(t(locale, _("Synced {count} commands {scope}."), count=len(synced), scope=scope)),
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
        invocation = await sd.Invocation.of(ctx)
        await invocation.reply(
            text_node(t(locale, _("Synced the tree to {synced}/{total}."), synced=ret, total=len(guilds))),
        )

    @commands.command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, ctx: Context[BotT]):
        """Sends the google sheets link"""
        invocation = await sd.Invocation.of(ctx)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await invocation.reply(
            link_node(
                t(locale, _("Build spreadsheet")),
                "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221",
                label=t(locale, _("Open spreadsheet")),
            ),
        )

    @commands.command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, ctx: Context[BotT]):
        """Sends the database link"""
        invocation = await sd.Invocation.of(ctx)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await invocation.reply(
            link_node(
                t(locale, _("Database")),
                "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc",
                label=t(locale, _("Open database")),
            ),
        )

    # Not `error`: that name now belongs to the stored-error lookup group, which is the command
    # someone reaches for while holding a reference a user reported.
    @commands.command(name="raise-error", aliases=["e"], hidden=True)
    @commands.is_owner()
    @managed_result(dismiss_on_success=True)
    async def raise_error(self, ctx: Context[BotT]) -> DocumentLike[sl.ComponentsV2Target]:
        """Raises an error for testing purposes."""
        msg = "This is a test error."
        raise ValueError(msg)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
