"""Create vote sessions from Discord and request their initial card refresh.

The post reconciler and vote renderer own subsequent publication and updates.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

import discord

from squid.bot._types import GuildMessageable
from squid.builds.domain import Status
from squid.builds.models import Build
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
    """Create or resume a pending build's review session and request card publication.

    A persisted build and submitter account are required. Session creation is
    serialized by build; repeating the call reuses its initial review session.
    The supplied channels determine option presets, while the renderer determines
    publication destinations from current settings.

    Returns:
        The session ID, or None when the supplied channel list is empty.
    """
    if build.id is None or build.submitter_account_id is None:
        msg = "A persisted build and submitter account are required for review."
        raise ValueError(msg)
    if build.submission_status != Status.PENDING:
        msg = "The build must be pending to post it."
        raise ValueError(msg)
    unique_channels = tuple({channel.id: channel for channel in channels}.values())
    if not unique_channels:
        # Options come from destination guilds, so no channels means no session.
        # The build is already committed: raising for this setup gap would report a
        # successful submission as failed and keep its event delivery retrying.
        logger.warning(
            "No configured Discord vote channel is available for build review; build %s has no vote card.",
            build.id,
            extra={"squid.build.id": build.id},
        )
        return None

    options: list[VoteOption] = []
    for guild_id in {channel.guild.id for channel in unique_channels}:
        options.extend((await bot.services.votes.emoji_preset(guild_id, VoteKind.BUILD)).options)

    settings = await bot.services.settings.get_runtime()
    session_id = await bot.services.votes.ensure_build_submission_vote(
        author_account_id=build.submitter_account_id,
        pass_threshold=settings.build_vote_pass_threshold,
        fail_threshold=settings.build_vote_fail_threshold,
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
    """Open a vote on deleting a logged message, rendered into an existing message.

    The card's location is a human decision — the channel the command was run in — so
    the caller sends the message and hands it over here, rather than the renderer
    inventing somewhere to put it.
    """
    if target_message.guild is None:
        msg = "Delete-log votes require a guild message."
        raise ValueError(msg)

    options = (await bot.services.votes.emoji_preset(target_message.guild.id, VoteKind.DELETE_LOG)).options
    settings = await bot.services.settings.get_runtime()
    session_id = await bot.services.votes.start_delete_log_vote(
        author_account_id=author_account_id,
        pass_threshold=settings.delete_vote_pass_threshold,
        fail_threshold=settings.delete_vote_fail_threshold,
        message_id=target_message.id,
        channel_id=target_message.channel.id,
        server_id=target_message.guild.id,
        options=options,
    )
    await bot.post_reconciler.adopt(published_message, "vote_session", str(session_id), "vote_card")
    await bot.refresh_posts("vote_session", str(session_id))
    return session_id
