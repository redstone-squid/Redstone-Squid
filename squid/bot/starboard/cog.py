"""Discord listeners and configuration commands for starboards."""

import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from whenever import Instant

import squid_ui_discord as sd
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.bot.starboard.screen import StarboardScreen
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_BOARD_RECOUNT,
    STARBOARD_BOARD_VIEW,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.posts.domain import starboard_entry_key
from squid.reactions.domain import ReactionActor
from squid.starboard.domain import OriginMessage, StarboardConfig

if TYPE_CHECKING:
    import squid.bot.app

STARBOARD_CAPABILITIES = (
    STARBOARD_BOARD_VIEW,
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_RECOUNT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
"""Every node this cog gates on.

The group gate is `any` of these rather than the view node alone: each is
separately grantable, so someone handed only `starboard.board.recount` has to be
able to reach the group that contains it.
"""


class StarboardCog[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Mirror messages after their weighted reactions cross configured thresholds."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self.service = bot.services.starboards
        self._debouncer = EntryDebouncer(self._refresh_key, bot.background_tasks)
        self.bot.reactions.subscribe(self)

    @override
    async def ui_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        await self._debouncer.close()

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        payload = event.payload
        if payload.guild_id is None or self.bot.user is None or payload.user_id == self.bot.user.id:
            return
        if not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        message = await event.message()
        member = await event.resolve_member()
        if message is None or member is None or member.bot or message.author.id == self.bot.user.id:
            return
        origin = self._origin(message)
        result = await self.service.record_vote(
            origin,
            ReactionActor(member.id, member.guild.id, frozenset(role.id for role in member.roles)),
            event.emoji,
        )
        if result.remove_reaction:
            with contextlib.suppress(discord.NotFound, discord.Forbidden):
                await message.remove_reaction(payload.emoji, member)
        self._schedule(result.keys)

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        payload = event.payload
        if payload.guild_id is None or not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        self._schedule(await self.service.withdraw_vote(payload.message_id, payload.user_id, event.emoji))

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        self._schedule(await self.service.clear_votes(event.payload.message_id))

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        self._schedule(await self.service.clear_votes(event.payload.message_id, event.emoji))

    @sd.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        self._schedule(await self.service.refresh(payload.message_id, force=True), force=True)

    @sd.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # A deleted *post* is tombstoned by the shared message listener and repaired by
        # the reconciler, so only the origin's disappearance is starboard business.
        self._schedule(await self.service.mark_origin_deleted(payload.message_id), force=True)

    @sd.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.service.disable_channel(channel.id)

    def _schedule(self, keys: Iterable[EntryKey], *, force: bool = False) -> None:
        for key in keys:
            self._debouncer.schedule(key, force=force)

    @staticmethod
    def _origin(message: discord.Message) -> OriginMessage:
        """Read the source-message facts starboard policy is decided from."""
        assert message.guild is not None
        channel_nsfw = bool(getattr(message.channel, "is_nsfw", lambda: False)())
        has_image = any((item.content_type or "").startswith("image/") for item in message.attachments) or any(
            embed.image.url or embed.thumbnail.url for embed in message.embeds
        )
        return OriginMessage(
            message.id,
            message.guild.id,
            message.channel.id,
            message.author.id,
            message.author.bot,
            Instant(message.created_at),
            is_nsfw=channel_nsfw,
            has_image=has_image,
        )

    async def _refresh_key(self, key: EntryKey, force: bool) -> None:
        """Nudge the reconciler for one entry.

        The debouncer exists for latency, not correctness: the score write already
        enqueued durable work, so a dropped nudge costs a few seconds rather than a
        missing post. Coalescing reaction storms is what it is actually for.
        """
        del force
        starboard_id, origin_message_id = key
        await self.bot.refresh_posts("starboard_entry", starboard_entry_key(starboard_id, origin_message_id))

    @app_commands.command(name="starboard", description="Configure this server's starboards")
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def starboard(self, interaction: discord.Interaction[BotT]) -> None:
        """Open capability-aware starboard configuration."""
        await enforce(interaction, *STARBOARD_CAPABILITIES, mode="any")
        guild = interaction.guild
        assert guild is not None

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        granted: set[PermissionNode] = set()
        for node in STARBOARD_CAPABILITIES:
            if await authorize(node):
                granted.add(node)

        async def create_board(channel_id: int, name: str, required: float) -> StarboardConfig:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValidationError(tr(t"That destination is not a text channel in this server."))
            permissions = channel.permissions_for(guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
                raise ValidationError(tr(t"I need View Channel, Send Messages, and Read Message History there."))
            return await self.service.create_starboard(guild.id, channel.id, name, required=required)

        await self.ui.respond(
            interaction,
            StarboardScreen(
                self.service,
                guild_id=guild.id,
                capabilities=frozenset(granted),
                authorize=authorize,
                create_board=create_board,
            ),
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(StarboardCog(bot))
