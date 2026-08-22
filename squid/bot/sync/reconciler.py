"""Drain durable Discord reconciliation work."""

import logging
from typing import TYPE_CHECKING, override

from discord.ext.commands import Cog

from squid.bot.topics import resource_topic
from squid.observability import trace_span
from squid.runtime import JobHandle
from squid.sync import ReconciliationJob

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class ReconciliationCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Repair Discord views from durable database-triggered work."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self._task: JobHandle | None = None

    @override
    async def cog_load(self) -> None:
        self._task = self.bot.background_tasks.start_periodic(
            self.process_reconciliation,
            name="discord-reconciliation",
            interval=15,
        )

    @override
    async def cog_unload(self) -> None:
        if self._task is not None:
            await self.bot.background_tasks.cancel(self._task)

    async def process_reconciliation(self) -> None:
        """Drain bounded Discord refresh work."""
        await self.bot.wait_until_ready()
        with trace_span("squid.background.reconciliation", {"squid.surface": "background_loop"}):
            for job in await self.bot.services.discord_reconciliation.claim():
                await self._process_job(job)

    async def _process_job(self, job: ReconciliationJob) -> None:
        """Render one resource, then acknowledge the job only if that succeeded.

        Deletion needs no branch of its own: a renderer reports a vanished resource as
        wanting no posts, and the diff loop removes whatever is left.
        """
        try:
            # The same reconciler the bot exposes for latency nudges, so a command and
            # this job cannot render a resource two different ways.
            await self.bot.post_reconciler.reconcile(job.resource_kind.post_kind, job.source_key, job.generation)
        except Exception as error:
            dead_lettered = await self.bot.services.discord_reconciliation.fail(job, error)
            if dead_lettered:
                logger.exception(
                    "Dead-lettered Discord reconciliation job after repeated failures",
                    extra={"resource_kind": job.resource_kind, "source_key": job.source_key},
                )
            return
        self.bot.topic_bus.publish(resource_topic(job.resource_kind.post_kind, job.source_key))
        await self.bot.services.discord_reconciliation.complete(job)
