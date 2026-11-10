"""Various admin commands for the bot."""

import json
import re
from typing import TYPE_CHECKING, Literal, Self

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Greedy
from pydantic import ValidationError

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.reactions import ReactionEvent, ReactionFilter, reaction_handler
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


class Admin[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Cog for admin commands."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.tags = bot.services.tags
        self.restrictions = bot.services.restrictions
        self._archive_header_pattern = re.compile(r"^<@!?(\d+)>.*wrote:")

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
        username_description = f" ({user.name} `<@{user.id}>`)" if user else ""
        reactions = message.reactions
        has_reactions = len(reactions) != 0

        content = f"{message.author.mention}{username_description} wrote:\n```\n{message.clean_content}```"
        if has_reactions:
            content += "\n" + " ".join(f"{reaction.emoji}:{reaction.count}" for reaction in reactions)
        content += "\nIf you are the author, you may delete this message by reacting with ❌."
        sent_message = await message.channel.send(
            content=content,
            embeds=message.embeds,
            files=[await attachment.to_file() for attachment in message.attachments],
            stickers=message.stickers,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=(message.author,), roles=False, replied_user=False
            ),
        )
        await sent_message.add_reaction("❌")
        await message.delete()

    @reaction_handler("add", filter=ReactionFilter(emojis=frozenset({"❌"})), recover=True)
    async def delete_archived_message(self, event: ReactionEvent) -> None:
        """Delete an archived copy when the original message's author reacts with ❌."""
        payload = event.payload
        message = await event.message(own=True)
        if message is None:
            return
        header = message.content.splitlines()[0] if message.content else ""
        match = self._archive_header_pattern.match(header)
        if not match:
            return
        author_id = int(match.group(1))
        if author_id != payload.user_id:
            return
        await message.delete()

    @sd.prefix_command(name="runtime-settings", hidden=True)
    @commands.is_owner()
    async def runtime_settings(
        self,
        request: sd.Request[Self],
        setting: str | None = None,
        *,
        value: str | None = None,
    ) -> sd.CommandResult:
        """Inspect settings or change one using a JSON value, e.g. welcome_forward_chance 0.25.

        Booleans use true/false; build_log_channel_ids accepts an array of IDs.
        Changes apply to subsequent operations. Vote defaults affect new sessions only.
        """
        service = self.bot.services.settings
        if setting is None:
            settings = await service.get_runtime()
            return text_node(json.dumps(settings.model_dump(mode="json"), indent=2))
        if value is None:
            settings = await service.get_runtime()
            if setting not in type(settings).model_fields:
                return text_node("Unknown runtime setting. Run runtime-settings to list available names.")
            return text_node(f"{setting}: {json.dumps(settings.model_dump(mode='json')[setting])}")
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD,
            str(request.user.id),
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            return text_node("Open /account and accept the current privacy notice before changing runtime settings.")
        try:
            settings = await service.update_runtime(
                {setting: json.loads(value)},
                actor_account_id=account.id,
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            return text_node(f"Invalid setting: {exc}")
        return text_node(f"Saved {setting}: {json.dumps(settings.model_dump(mode='json')[setting])}")

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
            count = len(synced)
            return text_node(tr(t"Synced {count} commands {scope}."))

        ret = 0
        for guild in guilds:
            try:
                await tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        synced = ret
        total = len(guilds)
        return text_node(tr(t"Synced the tree to {synced}/{total}."))

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
