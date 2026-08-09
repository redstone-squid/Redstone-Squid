"""Handlers reacting to one domain-event type each."""

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from squid.bot._types import GuildMessageable
from squid.bot.message_adapter import to_tracked_message
from squid.bot.utils.components import no_mentions
from squid.builds.domain import Status
from squid.events import DomainEvent

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class DomainEventHandler(Protocol):
    """React to one kind of recorded transition.

    Delivery is at-least-once, so `handle` must be safe to run more than once for
    the same event.
    """

    async def handle(self, event: DomainEvent) -> None: ...


class PostConfirmedBuildHandler:
    """Post a newly confirmed build to each guild's configured build channel."""

    def __init__(self, bot: "squid.bot.app.RedstoneSquid") -> None:
        self.bot = bot

    async def handle(self, event: DomainEvent) -> None:
        build = await self.bot.services.build_queries.get(event.aggregate_id)
        if build is None:
            logger.warning(
                "Cannot post a confirmed build that no longer exists",
                extra={"squid.build.id": event.aggregate_id},
            )
            return
        if build.submission_status != Status.CONFIRMED:
            # The build was confirmed and then changed again before this event was
            # drained; the later transition has its own event, so drop this one.
            return

        assert build.id is not None
        build_id = build.id
        handler = self.bot.for_build(build)

        # Skipping channels that already hold a tracked post is what makes redelivery
        # safe, including after a partial failure that posted to only some of them.
        posted = await self.bot.services.messages.list_for_build_purpose(build_id, "view_confirmed_build")
        already_posted = {record.channel_id for record in posted}
        channels = [channel for channel in await handler.get_channels_to_post_to() if channel.id not in already_posted]
        if not channels:
            return

        layout = await handler.render_layout()

        async def _send(channel: GuildMessageable) -> None:
            message = await channel.send(view=layout, allowed_mentions=no_mentions())
            await self.bot.services.messages.track(
                to_tracked_message(message),
                purpose="view_confirmed_build",
                build_id=build_id,
            )

        # Every send must settle before the delivery is acknowledged or retried, so a
        # slow send cannot still be in flight when a retry starts posting again.
        results = await asyncio.gather(*(_send(channel) for channel in channels), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result


def build_handler_registry(bot: "squid.bot.app.RedstoneSquid") -> dict[str, DomainEventHandler]:
    """Map each handled event type to its handler."""
    return {"build.confirmed": PostConfirmedBuildHandler(bot)}
