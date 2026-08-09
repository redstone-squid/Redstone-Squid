"""Drain the domain-event log for the Discord consumer."""

import logging
from typing import TYPE_CHECKING, override

from discord.ext import tasks
from discord.ext.commands import Cog

from squid.bot.events.handlers import DomainEventHandler, build_handler_registry
from squid.events import DomainEventDelivery
from squid.observability import trace_span

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

CONSUMER = "discord"
"""The consumer name this process claims deliveries under, seeded by migration."""


class DomainEventCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Run one-shot side effects for recorded state transitions."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.handlers: dict[str, tuple[DomainEventHandler, ...]] = build_handler_registry(bot)
        self.process_domain_events.start()

    @override
    async def cog_unload(self) -> None:
        self.process_domain_events.cancel()

    @tasks.loop(seconds=15)
    async def process_domain_events(self) -> None:
        """Dispatch bounded transition work to its handlers."""
        try:
            with trace_span("squid.background.domain_events", {"squid.surface": "background_loop"}):
                for delivery in await self.bot.services.domain_events.claim(CONSUMER):
                    await self._process(delivery)
        except Exception:
            logger.exception("Failed to process domain events")

    async def _process(self, delivery: DomainEventDelivery) -> None:
        handlers = self.handlers.get(delivery.event.event_type)
        if not handlers:
            # Every registered consumer receives every event, so an unhandled type is
            # normal rather than an error; acknowledge it instead of retrying forever.
            await self.bot.services.domain_events.complete(delivery)
            return
        try:
            # One failure retries the whole delivery, re-running the handlers that
            # already succeeded. That is why every handler must be idempotent.
            for handler in handlers:
                await handler.handle(delivery.event)
        except Exception as error:
            dead_lettered = await self.bot.services.domain_events.fail(delivery, error)
            if dead_lettered:
                logger.exception(
                    "Dead-lettered a domain event after repeated handler failures",
                    extra={
                        "squid.event.id": delivery.event.id,
                        "squid.event.type": delivery.event.event_type,
                    },
                )
            return
        await self.bot.services.domain_events.complete(delivery)

    @process_domain_events.before_loop
    async def before_process_domain_events(self) -> None:
        await self.bot.wait_until_ready()
