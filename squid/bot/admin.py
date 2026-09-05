"""Various admin commands for the bot."""

import re
from typing import TYPE_CHECKING, Literal, Self, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Greedy

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.reactions import ReactionEvent
from squid.bot.tags_view import TagsScreen
from squid.bot.ui import link_node, text_node
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import allows, enforce
from squid.core.i18n import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    MESSAGE_ARCHIVE_CREATE,
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)

if TYPE_CHECKING:
    import squid.bot.app


class Admin[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.tags = bot.services.tags
        self.restrictions = bot.services.restrictions
        self._archive_header_pattern = re.compile(r"^<@!?(\d+)>.*wrote:")
        self._reaction_subscription = self.bot.reactions.subscribe(
            type(self).__qualname__, add=self.on_reaction_add, recover_add=self.on_reaction_add
        )

    @override
    async def ui_unload(self) -> None:
        self._reaction_subscription.detach()

    @autocompletes(build_id="builds")
    @sd.command(name="tags", description="Browse, apply, propose, and moderate build tags")
    @app_commands.rename(build_id="build")
    async def tags_workspace(self, request: sd.Request[Self], build_id: int | None = None) -> TagsScreen:
        """Open the capability-aware build tag workspace."""
        nodes = (
            TAG_PROPOSAL_LIST,
            TAG_PROPOSAL_APPROVE,
            TAG_PROPOSAL_REJECT,
            TAG_PROPOSAL_ARCHIVE,
            RESTRICTION_ALIAS_CREATE,
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        granted: set[PermissionNode] = set()
        for node in nodes:
            if await authorize(node):
                granted.add(node)
        capabilities = frozenset(granted)
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD,
            str(request.user.id),
        )
        account_id = (
            account.id if account is not None and account.id is not None and not account.needs_consent_refresh else None
        )
        return TagsScreen(
            self.tags,
            self.restrictions,
            build_id=build_id,
            actor_account_id=account_id,
            capabilities=capabilities,
            authorize=authorize,
        )

    @sd.context_menu(
        name="Archive Message",
        defer="private",
        default_permissions=discord.Permissions(manage_messages=True),
    )
    async def archive_message_context(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Archive the message selected through Discord's Apps menu."""
        await enforce(request, MESSAGE_ARCHIVE_CREATE)
        if request.guild is None or message.guild != request.guild:
            return text_node(tr("That message is not from this server."))
        await self._archive_message(message)
        return text_node(tr("Message archived."))

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

    @sd.prefix_command(name="s", hidden=True)
    @commands.is_owner()
    async def sync(self, request: sd.Request[Self], guilds: Greedy[discord.Object], spec: Literal["~", "*", "^"] | None = None) -> sd.CommandResult:  # fmt: skip
        """Syncs the slash commands with the discord API."""
        tree = self.bot.tree
        if not guilds:
            if spec == "~":
                synced = await tree.sync(guild=request.guild)
            elif spec == "*":
                tree.copy_global_to(guild=request.guild)  # type: ignore
                synced = await tree.sync(guild=request.guild)
            elif spec == "^":
                tree.clear_commands(guild=request.guild)
                await tree.sync(guild=request.guild)
                synced = []
            else:
                synced = await tree.sync()

            scope = tr("globally") if spec is None else tr("to the current guild")
            return text_node(tr("Synced {count} commands {scope}.", count=len(synced), scope=scope))

        ret = 0
        for guild in guilds:
            try:
                await tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        return text_node(tr("Synced the tree to {synced}/{total}.", synced=ret, total=len(guilds)))

    @sd.prefix_command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Sends the google sheets link"""
        return link_node(
            tr("Build spreadsheet"),
            "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221",
            label=tr("Open spreadsheet"),
        )

    @sd.prefix_command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Sends the database link"""
        return link_node(
            tr("Database"),
            "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc",
            label=tr("Open database"),
        )

    # Not `error`: that name now belongs to the stored-error lookup group, which is the command
    # someone reaches for while holding a reference a user reported.
    @sd.prefix_command(name="raise-error", aliases=["e"], hidden=True, pending="Working…")
    @commands.is_owner()
    async def raise_error(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Raises an error for testing purposes."""
        msg = "This is a test error."
        raise ValueError(msg)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Admin(bot))
