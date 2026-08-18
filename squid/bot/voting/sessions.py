"""Starting vote sessions from Discord.

Creating a session is all that happens here. Publishing and re-rendering its cards
belong to `VoteSessionRenderer` and the reconcile loop, which is what let the session
classes — with their hand-synchronised message sets, ten-message ceiling, and
two-phase construction — be deleted outright.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

from squid.bot._types import GuildMessageable
from squid.builds.domain import Build, Status
from squid.voting.domain import VoteKind, VoteOption

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


async def ensure_build_review(
    bot: squid.bot.app.RedstoneSquid,
    build: Build,
    channels: Sequence[GuildMessageable],
) -> int | None:
    """Create or resume a build's initial review session and publish its cards.

    Safe to repeat: the session is created under an advisory lock keyed by build, and
    the reconciler posts one card per configured vote channel, filling any that a
    previous attempt missed. This is what replaced the stable-nonce send that used to
    deduplicate a retried delivery.

    Returns:
        The session id, or None when no guild has a vote channel configured and there is
        therefore nothing to review against.
    """
    if build.id is None or build.submitter_account_id is None:
        msg = "A persisted build and submitter account are required for review."
        raise ValueError(msg)
    if build.submission_status != Status.PENDING:
        msg = "The build must be pending to post it."
        raise ValueError(msg)
    unique_channels = tuple({channel.id: channel for channel in channels}.values())
    if not unique_channels:
        # An unconfigured vote channel is a setup gap, not a failed submission: the build is
        # already committed by every caller that gets here, and raising would both report a
        # successful submission as an error and make the event handler retry it forever. The
        # session is skipped rather than opened empty, since its options come from the guilds
        # of the channels it would be posted to.
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
    """Open a vote on deleting a logged message, rendered into an existing message.

    The card's location is a human decision — the channel the command was run in — so
    the caller sends the message and hands it over here, rather than the renderer
    inventing somewhere to put it.
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
