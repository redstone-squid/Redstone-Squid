"""Start build review and apply approved message deletions from recorded events."""

import contextlib
import logging
from typing import TYPE_CHECKING

import discord

from squid.bot.voting.sessions import configured_vote_channels, ensure_build_review
from squid.core.builds.domain import BuildStatus
from squid.core.events import DomainEvent, UnsupportedEventVersionError
from squid.core.voting.domain import DeleteLogVoteTarget, VoteSessionResult

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class PostSubmittedBuildHandler:
    """Create or resume Discord review delivery for a submitted build."""

    _SCHEMA_VERSIONS = frozenset({1, 2})

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self.bot = bot

    async def handle(self, event: DomainEvent) -> None:
        if event.schema_version not in self._SCHEMA_VERSIONS:
            msg = f"Unsupported build.submitted schema version {event.schema_version}"
            raise UnsupportedEventVersionError(msg)
        build = await self.bot.services.build_queries.get(event.aggregate_id)
        if build is None:
            logger.warning(
                "Cannot post a submitted build that no longer exists",
                extra={"squid.build.id": event.aggregate_id},
            )
            return
        if build.submission_status != BuildStatus.PENDING:
            return
        await ensure_build_review(self.bot, build, await configured_vote_channels(self.bot))


class DeleteVotedMessageHandler:
    """Delete the message a closed delete-log vote approved removing."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self.bot = bot

    async def handle(self, event: DomainEvent) -> None:
        snapshot = await self.bot.services.votes.get_session(event.aggregate_id)
        if snapshot is None or snapshot.is_open or snapshot.result is not VoteSessionResult.APPROVED:
            return
        target = snapshot.target
        if not isinstance(target, DeleteLogVoteTarget):
            return
        message = await self.bot.get_or_fetch_message(target.channel_id, target.message_id)
        if message is None:
            return
        # An already-deleted target is the expected state on redelivery.
        with contextlib.suppress(discord.NotFound):
            await message.delete()
