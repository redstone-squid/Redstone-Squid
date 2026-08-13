"""Drain the domain-event log for the Discord consumer."""

import logging
from typing import TYPE_CHECKING, override

from discord.ext.commands import Cog

from squid.bot.events.handlers import DomainEventHandler, build_handler_registry
from squid.events import DomainEventDelivery, UnsupportedEventVersionError
from squid.observability import trace_span
from squid.runtime import JobHandle

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

CONSUMER = "discord"
"""The consumer name this process claims deliveries under, seeded by migration."""
DELIVERY_REQUIRED_EVENTS = frozenset({"build.submitted"})


class DomainEventCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Run one-shot side effects for recorded state transitions."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.handlers: dict[str, tuple[DomainEventHandler, ...]] = build_handler_registry(bot)
        self._task: JobHandle | None = None

    @override
    async def cog_load(self) -> None:
        self._task = self.bot.background_tasks.start_periodic(
            self.process_domain_events,
            name="discord-domain-events",
            interval=15,
        )

    @override
    async def cog_unload(self) -> None:
        if self._task is not None:
            await self.bot.background_tasks.cancel(self._task)

    async def process_domain_events(self) -> None:
        """Dispatch bounded transition work to its handlers."""
        await self.bot.wait_until_ready()
        with trace_span("squid.background.domain_events", {"squid.surface": "background_loop"}):
            for delivery in await self.bot.services.domain_events.claim(CONSUMER):
                await self._process(delivery)

    async def _process(self, delivery: DomainEventDelivery) -> None:
        handlers = self.handlers.get(delivery.event.event_type)
        if not handlers:
            if delivery.event.event_type in DELIVERY_REQUIRED_EVENTS:
                error = RuntimeError(f"No Discord delivery handler for {delivery.event.event_type}")
                await self.bot.services.domain_events.fail(delivery, error)
                return
            # Every registered consumer receives every event, so an unhandled type is
            # normal rather than an error; acknowledge it instead of retrying forever.
            await self.bot.services.domain_events.complete(delivery)
            return
        try:
            # One failure retries the whole delivery, re-running the handlers that
            # already succeeded. That is why every handler must be idempotent.
            for handler in handlers:
                await handler.handle(delivery.event)
        except UnsupportedEventVersionError as error:
            await self.bot.services.domain_events.reject(delivery, error)
            logger.exception(
                "Rejected a Discord domain event with an unsupported schema version",
                extra={
                    "squid.event.id": delivery.event.id,
                    "squid.event.type": delivery.event.event_type,
                    "squid.event.schema_version": delivery.event.schema_version,
                },
            )
            return
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
