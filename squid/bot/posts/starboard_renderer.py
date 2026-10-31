"""Deciding whether a starboard entry should be mirrored, and what it shows."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, final

import discord

from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale
from squid.bot.posts.renderer import DesiredPost
from squid.bot.starboard.render import starboard_layout
from squid.bot.utils.components import no_mentions
from squid.posts.domain import ResourceKind
from squid.starboard.domain import entry_should_be_posted

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@final
class StarboardEntryRenderer[BotT: "squid.bot.app.RedstoneSquid"]:
    """Mirror a message into a starboard channel while its score warrants it."""

    resource_kind: ResourceKind = "starboard_entry"
    repost_if_deleted: bool = True
    """A starboard post is a mirror of something else, so a missing one is damage.

    This is the opposite of a build card, where deletion is a moderator's decision.
    Both policies now sit on the renderer instead of being re-derived per surface.
    """

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        starboard_id, _, origin_message_id = resource_key.partition(":")
        state = await self.bot.services.starboards.entry_state(int(starboard_id), int(origin_message_id))
        if state is None:
            return None

        posts = await self.bot.services.posts.list_for_resource(self.resource_kind, resource_key)
        currently_posted = any(post.is_live for post in posts)
        if not entry_should_be_posted(
            state.config,
            state.entry.score,
            origin_present=state.origin.present,
            currently_posted=currently_posted,
        ):
            return ()

        destination = await self._channel(state.config.channel_id)
        origin = await self.bot.get_or_fetch_message(state.origin.channel_id, state.origin.id)
        if destination is None or origin is None or _unsafe_nsfw(origin.channel, destination):
            return ()

        mentions = (
            discord.AllowedMentions(everyone=False, roles=False, users=(origin.author,), replied_user=False)
            if state.config.ping_author
            else no_mentions()
        )
        locale = await resolve_locale(origin, self.bot.services.settings)
        return [
            DesiredPost(
                channel_id=state.config.channel_id,
                guild_id=state.config.guild_id,
                surface="starboard_entry",
                presentation=starboard_layout(state, origin, locale=locale),
                allowed_mentions=mentions,
            )
        ]

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        """Seed the board's own reaction aliases so readers can vote from the mirror."""
        starboard_id, _, origin_message_id = resource_key.partition(":")
        state = await self.bot.services.starboards.entry_state(int(starboard_id), int(origin_message_id))
        if state is None:
            return
        for item in state.config.emojis:
            enabled = state.config.autoreact_upvote if item.direction == "up" else state.config.autoreact_downvote
            if not enabled:
                continue
            try:
                await message.add_reaction(item.emoji)
            except discord.Forbidden:
                logger.debug("Missing permission to autoreact on a starboard post")
                return

    async def _channel(self, channel_id: int) -> GuildMessageable | None:
        channel = await self.bot.get_or_fetch_messageable_channel(channel_id)
        return channel if isinstance(channel, GuildMessageable) else None


def _unsafe_nsfw(source: discord.abc.Messageable, destination: GuildMessageable) -> bool:
    """Whether mirroring would move NSFW content into a channel that is not marked so."""
    source_nsfw = bool(getattr(source, "is_nsfw", lambda: False)())
    destination_nsfw = bool(getattr(destination, "is_nsfw", lambda: False)())
    return source_nsfw and not destination_nsfw
