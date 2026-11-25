"""Dispatch recorded application events to Discord feature handlers."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, override

from discord.ext.commands import Cog

from squid.bot.events.spec import extension
from squid.core.actors import ActingContext
from squid.core.events.dispatcher import DomainEventRunner, EventHandler
from squid.core.observability import TraceSurface, trace_span
from squid.runtime import JobHandle

if TYPE_CHECKING:
    import squid.bot.app


CONSUMER = "discord"
"""The consumer name this process claims deliveries under, seeded by migration."""
DELIVERY_REQUIRED_EVENTS = frozenset({"build.submitted"})


class DomainEventCog[BotT: squid.bot.app.RedstoneSquid](Cog):
    """Deliver recorded transitions to Discord handlers with retries."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self._handlers: dict[str, tuple[EventHandler, ...]] = {}
        self._batch_lock = asyncio.Lock()
        self._runner = DomainEventRunner(
            bot.services.domain_events, CONSUMER, self._handlers, required=DELIVERY_REQUIRED_EVENTS
        )
        self._task: JobHandle | None = None

    def subscribe(self, handlers: Mapping[str, EventHandler]) -> Callable[[], Awaitable[None]]:
        """Register handlers and return a callback to detach them between batches."""
        registrations = tuple((event_type, partial(handler)) for event_type, handler in handlers.items())
        for event_type, handler in registrations:
            self._handlers[event_type] = (*self._handlers.get(event_type, ()), handler)

        async def detach() -> None:
            async with self._batch_lock:
                for event_type, handler in registrations:
                    remaining = tuple(
                        current for current in self._handlers.get(event_type, ()) if current is not handler
                    )
                    if remaining:
                        self._handlers[event_type] = remaining
                    else:
                        self._handlers.pop(event_type, None)

        return detach

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
        """Process one batch after the bot is ready.

        Handler failures follow the delivery retry policy. Errors in claiming or
        acknowledging deliveries propagate and stop the batch.
        """
        await self.bot.wait_until_ready()
        with trace_span("squid.background.domain_events", {"squid.surface": TraceSurface.BACKGROUND_LOOP}):
            async with self._batch_lock:
                await self._runner.process_batch(context=ActingContext.worker("discord-domain-events"))


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Register the domain-event dispatcher."""
    ctx = bot.extension_manager.setup_context(extension)
    cog = DomainEventCog(bot)
    await bot.add_cog(cog)
    ctx.publish(cog)
