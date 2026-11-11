"""Discord listeners and configuration commands for starboards."""

import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar, Self, override

import discord
from discord import app_commands
from whenever import Instant

import squid_ui_discord as sd
from squid.bot.reactions import ReactionClearEvent, ReactionEvent, ReactionFilter, reaction_handler
from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.bot.starboard.screen import StarboardScreen
from squid.bot.utils.permissions import allows, current_edit_actor, enforce, hide_unless
from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.messages.models import Message
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
from squid.starboard.models import OriginMessage

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


class StarboardCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Mirror messages after their weighted reactions cross configured thresholds."""

    reaction_filter: ClassVar[ReactionFilter] = ReactionFilter(guild_only=True)

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self.service = bot.services.starboards
        self._debouncer = EntryDebouncer(self._refresh_key, bot.background_tasks)

    @override
    async def ui_unload(self) -> None:
        await self._debouncer.close()

    @reaction_handler("add")
    async def on_reaction_add(self, event: ReactionEvent) -> None:
        payload = event.payload
        assert payload.guild_id is not None  # The subscription is guild-only.
        if not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        message = await event.message(own=False)
        member = await event.member(humans_only=True)
        if message is None or member is None:
            return
        origin = self._origin(message, member.guild)
        result = await self.service.record_vote(
            origin,
            ReactionActor(member.id, member.guild.id, frozenset(role.id for role in member.roles)),
            event.emoji,
        )
        if result.remove_reaction:
            with contextlib.suppress(discord.NotFound, discord.Forbidden):
                await message.remove_reaction(payload.emoji, member)
        self._schedule(result.keys)

    @reaction_handler("remove")
    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        payload = event.payload
        assert payload.guild_id is not None  # The subscription is guild-only.
        if not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        self._schedule(await self.service.withdraw_vote(payload.message_id, payload.user_id, event.emoji))

    @reaction_handler("clear")
    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        self._schedule(await self.service.clear_votes(event.payload.message_id))

    @reaction_handler("clear_emoji")
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
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        # A channel purge arrives only as this event, never as individual deletes.
        for message_id in payload.message_ids:
            self._schedule(await self.service.mark_origin_deleted(message_id), force=True)

    async def recount(self, message: discord.Message) -> tuple[EntryKey, ...]:
        """Replace one source message's starboard votes with the reactions it has now.

        Use this to repair votes lost while the bot was offline. It reads every user of
        every starboard emoji on the message, and may fetch members missing from the cache,
        so callers must limit how often it runs.

        Returns:
            The entries whose score was recomputed; their posts are refreshed. A message
            outside a guild has no starboards, so it returns an empty tuple.
        """
        guild = message.guild
        if guild is None:
            return ()
        reactions: list[tuple[ReactionActor, str]] = []
        for reaction in message.reactions:
            emoji = str(reaction.emoji)
            if not await self.service.is_relevant_emoji(guild.id, emoji):
                continue
            async for user in reaction.users(limit=None):
                if user.bot:
                    continue
                member = user if isinstance(user, discord.Member) else await self._member(guild, user.id)
                if member is not None:
                    actor = ReactionActor(member.id, guild.id, frozenset(role.id for role in member.roles))
                    reactions.append((actor, emoji))
        keys = await self.service.recount(self._origin(message, guild), tuple(reactions))
        self._schedule(keys, force=True)
        return keys

    @staticmethod
    async def _member(guild: discord.Guild, user_id: int) -> discord.Member | None:
        """Return a cached or fetched member, or None if they have left the guild."""
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    def _schedule(self, keys: Iterable[EntryKey], *, force: bool = False) -> None:
        for key in keys:
            self._debouncer.schedule(key, force=force)

    @staticmethod
    def _origin(message: discord.Message, guild: discord.Guild) -> OriginMessage:
        """Read the source-message details starboard policy is decided from."""
        channel_nsfw = bool(getattr(message.channel, "is_nsfw", lambda: False)())
        has_image = any((item.content_type or "").startswith("image/") for item in message.attachments) or any(
            embed.image.url or embed.thumbnail.url for embed in message.embeds
        )
        return OriginMessage(
            id=message.id,
            author_is_bot=message.author.bot,
            is_nsfw=channel_nsfw,
            has_image=has_image,
            message=Message(
                id=message.id,
                guild_id=guild.id,
                channel_id=message.channel.id,
                author_id=message.author.id,
                created_at=Instant(message.created_at),
            ),
        )

    async def _refresh_key(self, key: EntryKey, force: bool) -> None:
        """Request a prompt refresh after the durable score update.

        Reaction bursts share a debounced refresh. Database-triggered work remains
        available if the process stops before this callback runs.
        """
        del force
        starboard_id, origin_message_id = key
        await self.bot.refresh_posts("starboard_entry", starboard_entry_key(starboard_id, origin_message_id))

    @sd.command(name="starboard", description="Configure this server's starboards")
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def starboard(self, request: sd.Request[Self]) -> StarboardScreen:
        """Open capability-aware starboard configuration."""
        await enforce(request, *STARBOARD_CAPABILITIES, mode="any")
        guild = request.guild
        assert guild is not None

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        granted: set[PermissionNode] = set()
        for node in STARBOARD_CAPABILITIES:
            if await authorize(node):
                granted.add(node)

        async def validate_channel(channel_id: int) -> None:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValidationError(
                    tr(t"starboard-destination-must-be-text = That destination is not a text channel in this server.")
                )
            permissions = channel.permissions_for(guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
                raise ValidationError(
                    tr(
                        t"starboard-channel-permissions-required = I need View Channel, Send "
                        t"Messages, and Read Message History there."
                    )
                )

        async def actor() -> Actor:
            return await current_edit_actor(request)

        return StarboardScreen(
            self.service,
            guild_id=guild.id,
            capabilities=frozenset(granted),
            authorize=authorize,
            edits=self.bot.services.edits,
            actor=actor,
            validate_channel=validate_channel,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(StarboardCog(bot))
