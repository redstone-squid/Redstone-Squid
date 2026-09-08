"""Starting vote sessions from Discord.

Only creation lives here; publishing and re-rendering cards belong to `VoteSessionRenderer` and the reconcile
loop.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

import discord

from squid.bot._types import GuildMessageable
from squid.builds.domain import Build, Status
from squid.voting.domain import VoteKind, VoteOption

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


async def configured_vote_channels(bot: squid.bot.app.RedstoneSquid) -> list[GuildMessageable]:
    """Every vote channel the bot can currently see, one per guild that set one."""
    configured = await bot.services.settings.get_many((guild.id for guild in bot.guilds), "Vote")
    resolved = (bot.get_channel(channel_id) for channel_id in configured.values() if channel_id is not None)
    return cast(list[GuildMessageable], [channel for channel in resolved if channel is not None])


async def ensure_build_review(
    bot: squid.bot.app.RedstoneSquid,
    build: Build,
    channels: Sequence[GuildMessageable],
) -> int | None:
    """Create or resume a build's review session and publish its cards.

    Safe to repeat: the session is created under an advisory lock keyed by build, and the reconciler fills any
    vote channel a previous attempt missed. Returns the session id, or None when `channels` is empty.

    Raises:
        ValueError: the build is unsaved, has no submitter account, or is not pending.
    """
    if build.id is None or build.submitter_account_id is None:
        msg = "A persisted build and submitter account are required for review."
        raise ValueError(msg)
    if build.submission_status != Status.PENDING:
        msg = "The build must be pending to post it."
        raise ValueError(msg)
    unique_channels = tuple({channel.id: channel for channel in channels}.values())
    if not unique_channels:
        # A setup gap, not a failed submission: the build is already committed, and raising would make the event
        # handler retry forever. The session is skipped rather than opened empty, since its options come from the
        # guilds of the channels it would be posted to.
        logger.warning(
            "No configured Discord vote channel is available for build review; build %s has no vote card.",
            build.id,
            extra={"squid.build.id": build.id},
        )
        return None

    options: list[VoteOption] = []
    for guild_id in {channel.guild.id for channel in unique_channels}:
        options.extend((await bot.services.votes.emoji_preset(guild_id, VoteKind.BUILD)).options)

    session_id = await bot.services.votes.ensure_build_submission_vote(
        author_account_id=build.submitter_account_id,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=build.id,
        changes=[("submission_status", Status.PENDING, Status.CONFIRMED)],
        options=options,
    )
    await bot.refresh_posts("vote_session", str(session_id))
    return session_id


async def start_delete_log_vote(
    bot: squid.bot.app.RedstoneSquid,
    *,
    author_account_id: int,
    target_message: discord.Message,
    published_message: discord.Message,
) -> int:
    """Open a vote on deleting a logged message, rendered into `published_message`.

    The card's location is the channel the command was run in, so the caller sends the message and hands it over
    rather than the renderer choosing a place.

    Raises:
        ValueError: `target_message` is not in a guild.
    """
    if target_message.guild is None:
        msg = "Delete-log votes require a guild message."
        raise ValueError(msg)

    options = (await bot.services.votes.emoji_preset(target_message.guild.id, VoteKind.DELETE_LOG)).options
    session_id = await bot.services.votes.start_delete_log_vote(
        author_account_id=author_account_id,
        pass_threshold=3,
        fail_threshold=-3,
        message_id=target_message.id,
        channel_id=target_message.channel.id,
        server_id=target_message.guild.id,
        options=options,
    )
    await bot.post_reconciler.adopt(published_message, "vote_session", str(session_id), "vote_card")
    await bot.refresh_posts("vote_session", str(session_id))
    return session_id
