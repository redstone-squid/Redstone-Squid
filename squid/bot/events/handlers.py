"""Handlers reacting to one domain-event type each."""

import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

import discord

from squid.builds.domain import Status
from squid.events import DomainEvent, UnsupportedEventVersionError
from squid.voting.domain import DeleteLogVoteTarget, VoteSessionResult

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class DomainEventHandler(Protocol):
    """React to one kind of recorded transition; registered by event type in `build_handler_registry`."""

    async def handle(self, event: DomainEvent) -> None:
        """React to one delivery of `event`.

        Delivery is at-least-once and a sibling handler's failure retries the whole delivery, so
        this must be safe to run more than once for the same event. Raising
        `UnsupportedEventVersionError` rejects the delivery outright; any other exception retries it
        until the queue dead-letters it.
        """
        ...


class PostSubmittedBuildHandler:
    """Create or resume Discord review delivery for a submitted build.

    A build that has been deleted or has left `PENDING` since the event was recorded is skipped, so
    a redelivery cannot repost a decided build.

    Raises:
        UnsupportedEventVersionError: The event carries a schema version this handler cannot read.
    """

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
        if build.submission_status != Status.PENDING:
            return
        await self.bot.for_build(build).post_for_voting()


class DeleteVotedMessageHandler:
    """Delete the message a closed delete-log vote approved removing.

    Does nothing unless the session is closed, approved and aimed at a delete-log target; the vote
    is re-read rather than trusted from the event.
    """

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self.bot = bot

    async def handle(self, event: DomainEvent) -> None:
        snapshot = await self.bot.services.votes.get_session_by_id(event.aggregate_id)
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


def build_handler_registry(bot: squid.bot.app.RedstoneSquid) -> dict[str, tuple[DomainEventHandler, ...]]:
    """Map each handled event type to the handlers that react to it."""
    return {
        "build.submitted": (PostSubmittedBuildHandler(bot),),
        # `build.confirmed` needs no handler: confirming a build updates its row, which enqueues a
        # Discord sync job, and the reconciler publishes the card. Posting from the event too would
        # race the reconciler.
        "vote_session.closed": (DeleteVotedMessageHandler(bot),),
    }
