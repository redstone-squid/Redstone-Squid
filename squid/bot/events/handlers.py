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
    """React to one kind of recorded transition.

    Delivery is at-least-once, so `handle` must be safe to run more than once for
    the same event.
    """

    async def handle(self, event: DomainEvent) -> None: ...


class PostSubmittedBuildHandler:
    """Create or resume Discord review delivery for a submitted build."""

    _SCHEMA_VERSIONS = frozenset({1, 2})

    def __init__(self, bot: "squid.bot.app.RedstoneSquid") -> None:
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
    """Delete the message a closed delete-log vote approved removing."""

    def __init__(self, bot: "squid.bot.app.RedstoneSquid") -> None:
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


def build_handler_registry(bot: "squid.bot.app.RedstoneSquid") -> dict[str, tuple[DomainEventHandler, ...]]:
    """Map each handled event type to the handlers that react to it."""
    return {
        "build.submitted": (PostSubmittedBuildHandler(bot),),
        # `build.confirmed` needs no handler: confirming a build updates its row, which
        # enqueues a Discord sync job, and the reconciler publishes the card. Posting it
        # from the event as well raced the reconciler and needed its own idempotency check.
        "vote_session.closed": (DeleteVotedMessageHandler(bot),),
    }
