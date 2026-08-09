"""Drain durable Discord and search-projection reconciliation work."""

import logging
from typing import TYPE_CHECKING, override

from discord.ext import tasks
from discord.ext.commands import Cog

from squid.bot.voting.build_session import BuildVoteSession
from squid.bot.voting.delete_log_session import DeleteLogVoteSession
from squid.bot.voting.generic_session import GenericVoteSession
from squid.observability import trace_span
from squid.sync import SyncJob

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class ReconciliationCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Repair Discord views from durable database-triggered work."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.process_reconciliation.start()

    @override
    async def cog_unload(self) -> None:
        self.process_reconciliation.cancel()

    @tasks.loop(seconds=15)
    async def process_reconciliation(self) -> None:
        """Drain bounded Discord refresh and search projection work."""
        try:
            with trace_span("squid.background.reconciliation", {"squid.surface": "background_loop"}):
                for job in await self.bot.services.discord_sync.claim():
                    await self._process_job(job)
                await self.bot.services.refresh_search_index()
        except Exception:
            logger.exception("Failed to process reconciliation work")

    async def _process_job(self, job: SyncJob) -> None:
        try:
            if job.action == "delete":
                logger.warning(
                    "Cannot remove an untracked Discord resource after database deletion",
                    extra={"resource_kind": job.resource_kind, "source_key": job.source_key},
                )
            elif job.resource_kind == "build":
                await self._refresh_build(int(job.source_key))
            else:
                await self._refresh_vote(int(job.source_key))
        except Exception as error:
            dead_lettered = await self.bot.services.discord_sync.fail(job, error)
            if dead_lettered:
                logger.exception(
                    "Dead-lettered Discord reconciliation job after repeated failures",
                    extra={"resource_kind": job.resource_kind, "source_key": job.source_key},
                )
            return
        await self.bot.services.discord_sync.complete(job)

    async def _refresh_build(self, build_id: int) -> None:
        build = await self.bot.services.build_queries.get(build_id)
        if build is not None:
            await self.bot.for_build(build).update_messages()

    async def _refresh_vote(self, vote_session_id: int) -> None:
        """Re-render a vote session's messages.

        Acting on a closed session's outcome belongs to the `vote_session.closed`
        domain event, not here: this queue coalesces, so it can say a session changed
        but not that it closed, and a re-render must stay safe to repeat.
        """
        snapshot = await self.bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None:
            return
        if snapshot.kind == "build":
            session = await BuildVoteSession.from_id(self.bot, vote_session_id)
        elif snapshot.kind == "delete_log":
            session = await DeleteLogVoteSession.from_id(self.bot, vote_session_id)
        else:
            session = await GenericVoteSession.from_id(self.bot, vote_session_id)
        if session is not None:
            await session.update_messages()

    @process_reconciliation.before_loop
    async def before_process_reconciliation(self) -> None:
        await self.bot.wait_until_ready()
