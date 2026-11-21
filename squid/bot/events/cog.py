"""Run Discord side effects for recorded application events.

The Discord consumer receives its own delivery of each domain event. Handlers create or resume
review delivery when a build is submitted and remove a message when a delete-log vote approves it.
The dispatcher acknowledges the delivery after its handlers succeed; a retry runs those handlers
again. Post content is kept current separately by the reconciliation workflow in squid.bot.sync.
"""

import logging
from typing import TYPE_CHECKING, override

from discord.ext.commands import Cog

from squid.bot.events.handlers import build_handler_registry
from squid.core.events.dispatcher import DomainEventRunner
from squid.observability import TraceSurface, trace_span
from squid.runtime import JobHandle

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

CONSUMER = "discord"
"""The consumer name this process claims deliveries under, seeded by migration."""
DELIVERY_REQUIRED_EVENTS = frozenset({"build.submitted"})


class DomainEventCog[BotT: squid.bot.app.RedstoneSquid](Cog):
    """Deliver recorded transitions to Discord handlers with retries."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self._runner = DomainEventRunner(
            bot.services.domain_events, CONSUMER, build_handler_registry(bot), required=DELIVERY_REQUIRED_EVENTS
        )
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
        """Process one batch after the bot is ready.

        Handler failures follow the delivery retry policy. Errors in claiming or
        acknowledging deliveries propagate and stop the batch.
        """
        await self.bot.wait_until_ready()
        with trace_span("squid.background.domain_events", {"squid.surface": TraceSurface.BACKGROUND_LOOP}):
            await self._runner.process_batch()


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Register the domain-event dispatcher."""
    await bot.add_cog(DomainEventCog(bot))
