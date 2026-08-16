"""Deciding where a vote session's cards live and what they say."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, final

import discord

from squid.bot.posts.renderer import DesiredPost
from squid.bot.voting.rendering import render_build_review, render_delete_log, render_generic_poll
from squid.core.concurrency import DISCORD_FANOUT_LIMIT, settle_all
from squid.posts.domain import ResourceKind
from squid.voting.domain import BuildVoteTarget, DeleteLogVoteTarget, VoteSessionSnapshot

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


@final
class VoteSessionRenderer[BotT: "squid.bot.app.RedstoneSquid"]:
    """Keep a vote session's cards current wherever they were published.

    Publication location is not derived the same way for every kind. A build review
    belongs in each guild's configured vote channel, so a guild that has none yet gets
    one. A delete-log vote and a generic poll were placed by a person in a channel they
    chose, so those are rendered where they already are and published explicitly.
    """

    resource_kind: ResourceKind = "vote_session"
    repost_if_deleted: bool = False
    """Someone removing a vote card ends the vote's visibility on purpose."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        snapshot = await self.bot.services.votes.get_session_by_id(int(resource_key))
        if snapshot is None:
            return None
        if snapshot.kind == "build":
            return await self._build_review(snapshot)
        if snapshot.kind == "delete_log":
            return await self._delete_log(snapshot)
        return await self._generic_poll(snapshot)

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        """Seed the reactions the session is voted on with."""
        snapshot = await self.bot.services.votes.get_session_by_id(int(resource_key))
        if snapshot is None or snapshot.status == "closed":
            return
        guild_id = message.guild.id if message.guild is not None else 0
        # settle_all, not gather: one channel denying reactions must not abort the batch
        # and leave its siblings unawaited.
        outcomes = await settle_all(
            [_add_reaction(message, option.emoji) for option in snapshot.options_for_guild(guild_id)],
            limit=DISCORD_FANOUT_LIMIT,
        )
        forbidden = sum(1 for outcome in outcomes if isinstance(outcome, discord.Forbidden))
        if forbidden:
            logger.debug("Missing permission to add %s vote reaction(s)", forbidden)
        for outcome in outcomes:
            if isinstance(outcome, Exception) and not isinstance(outcome, discord.Forbidden):
                logger.warning("Failed to add a vote reaction", exc_info=outcome)

    async def _build_review(self, snapshot: VoteSessionSnapshot) -> Sequence[DesiredPost]:
        target = snapshot.target
        if not isinstance(target, BuildVoteTarget):
            logger.warning("A build review session has no build", extra={"squid.vote_session.id": snapshot.id})
            return ()
        build = await self.bot.services.build_queries.get(target.build_id)
        if build is None:
            return ()

        handler = self.bot.for_build(build)
        channels = await self._published_channels(snapshot)
        if snapshot.status == "open":
            # Fill guilds that have a vote channel but no card yet, which is what makes
            # a retried or partially delivered submission complete itself.
            channels.update({channel.id: channel.guild.id for channel in await handler.get_channels_to_post_to()})

        posts: list[DesiredPost] = []
        for channel_id, guild_id in channels.items():
            # A fresh container per post: rendering mutates it, and the guild decides
            # which emoji the instructions name.
            container = await handler.render_container()
            posts.append(
                DesiredPost(
                    channel_id=channel_id,
                    guild_id=guild_id,
                    surface="build_review",
                    layout=render_build_review(container, snapshot, guild_id),
                )
            )
        return posts

    async def _delete_log(self, snapshot: VoteSessionSnapshot) -> Sequence[DesiredPost]:
        target = snapshot.target
        content = ""
        if isinstance(target, DeleteLogVoteTarget):
            message = await self.bot.get_or_fetch_message(target.channel_id, target.message_id)
            content = "" if message is None else message.content
        layout = render_delete_log(snapshot, content)
        return [
            DesiredPost(channel_id=channel_id, guild_id=guild_id, surface="vote_card", layout=layout)
            for channel_id, guild_id in (await self._published_channels(snapshot)).items()
        ]

    async def _generic_poll(self, snapshot: VoteSessionSnapshot) -> Sequence[DesiredPost]:
        if snapshot.poll is None:
            return ()
        layout = render_generic_poll(snapshot)
        return [
            DesiredPost(channel_id=channel_id, guild_id=guild_id, surface="vote_card", layout=layout)
            for channel_id, guild_id in (await self._published_channels(snapshot)).items()
        ]

    async def _published_channels(self, snapshot: VoteSessionSnapshot) -> dict[int, int]:
        """Channels this session already has live posts in, mapped to their guild."""
        posts = await self.bot.services.posts.list_for_resource("vote_session", str(snapshot.id))
        live = {post.channel_id for post in posts if post.is_live}
        return {message.channel_id: message.guild_id for message in snapshot.messages if message.channel_id in live}


def _add_reaction(message: discord.Message, emoji: str):
    async def add() -> None:
        await message.add_reaction(emoji)

    return add
