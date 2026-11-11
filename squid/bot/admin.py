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
from squid.bot.utils.permissions import allows, enforce, requires, subject_for_interaction
from squid.core.i18n import tr
from squid.edits.domain import EditActor, EditCommand, EditValues, Revision
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    MESSAGE_ARCHIVE_CREATE,
    RESTRICTION_ALIAS_CREATE,
    SETTINGS_RUNTIME_EDIT,
    SETTINGS_RUNTIME_VIEW,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET

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
            return text_node(tr(t"admin-that-message-is-not-from-this-server = That message is not from this server."))
        await self._archive_message(message)
        return text_node(tr(t"admin-message-archived = Message archived."))

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

    @sd.command(name="runtime-settings", description="View or change global runtime settings", defer="private")
    async def runtime_settings_slash(
        self,
        request: sd.Request[Self],
        setting: str | None = None,
        value: str | None = None,
        revision: int | None = None,
    ) -> sd.CommandResult:
        return await self._runtime_settings(request, setting, value=value, revision=revision)

    @sd.prefix_command(name="runtime-settings")
    @requires(SETTINGS_RUNTIME_VIEW, SETTINGS_RUNTIME_EDIT, mode="any")
    async def runtime_settings(
        self,
        request: sd.Request[Self],
        setting: str | None = None,
        *,
        value: str | None = None,
        revision: int | None = None,
    ) -> sd.CommandResult:
        """View or change global runtime settings with JSON values."""
        return await self._runtime_settings(request, setting, value=value, revision=revision)

    async def _runtime_settings(
        self,
        request: sd.Request[Self],
        setting: str | None = None,
        *,
        value: str | None = None,
        revision: int | None = None,
    ) -> sd.CommandResult:
        """Inspect settings or change one using a JSON value, e.g. welcome_forward_chance 0.25.

        Booleans use true/false; build_log_channel_ids accepts an array of IDs.
        Changes apply to subsequent operations. Vote defaults affect new sessions only.
        """
        await enforce(request, SETTINGS_RUNTIME_EDIT if value is not None else SETTINGS_RUNTIME_VIEW)
        service = self.bot.services.settings
        if value is not None and setting is None:
            return text_node("Choose a setting to change.")
        if setting is None:
            settings, current_revision = await service.runtime_snapshot()
            return text_node(json.dumps(settings.model_dump(mode="json") | {"revision": current_revision}, indent=2))
        if value is None:
            settings, current_revision = await service.runtime_snapshot()
            if setting not in type(settings).model_fields:
                return text_node("Unknown runtime setting. Run runtime-settings to list available names.")
            return text_node(
                f"{setting}: {json.dumps(settings.model_dump(mode='json')[setting])} (revision {current_revision})"
            )
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD,
            str(request.user.id),
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            return text_node("Open /account and accept the current privacy notice before changing runtime settings.")
        if revision is None or revision < 1:
            return text_node("Read runtime-settings first, then supply its revision when saving.")
        try:
            result = await self.bot.services.edits.execute(
                EditCommand(
                    RUNTIME_EDIT, (Revision(RUNTIME_TARGET, revision),), EditValues.of({setting: json.loads(value)})
                ),
                EditActor(await subject_for_interaction(request), discord_user_id=request.user.id),
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            return text_node(f"Invalid setting: {exc}")
        assert result.receipt is not None
        return text_node(
            f"Saved {setting}: {json.dumps(result.receipt.after.unpack()[setting])} (revision {result.outcome.revisions[0].value})"
        )

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

            scope = (
                tr(t"admin-globally = globally")
                if spec is None
                else tr(t"admin-to-the-current-guild = to the current guild")
            )
            count = len(synced)
            return text_node(tr(t"admin-synced-commands = Synced {count} commands {scope}."))

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
        return text_node(tr(t"admin-synced-the-tree-to = Synced the tree to {synced}/{total}."))

    @sd.prefix_command(name="gdb", hidden=True)
    @commands.is_owner()
    async def get_sheets_link(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Sends the google sheets link"""
        return link_node(
            tr(t"admin-build-spreadsheet = Build spreadsheet"),
            "https://docs.google.com/spreadsheets/d/1BiyHD6PE1Jyn1EtlT0o2DqciUzWPSdwHmeRcUJtanUs/edit#gid=2075219221",
            label=tr(t"admin-open-spreadsheet = Open spreadsheet"),
        )

    @sd.prefix_command(name="db", hidden=True)
    @commands.is_owner()
    async def get_database_link(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Sends the database link"""
        return link_node(
            tr(t"admin-database = Database"),
            "https://supabase.com/dashboard/project/jnushtruzgnnmmxabsxi/editor/29424?sort=submission_id%3Aasc",
            label=tr(t"admin-open-database = Open database"),
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
