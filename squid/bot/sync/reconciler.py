"""Apply durable desired Discord message projections."""

import contextlib
import logging
from typing import TYPE_CHECKING, override

import discord
from discord.ext.commands import Cog

from squid.bot.voting.build_session import BuildVoteSession
from squid.bot.voting.delete_log_session import DeleteLogVoteSession
from squid.bot.voting.generic_session import GenericVoteSession
from squid.observability import trace_span
from squid.runtime import JobHandle
from squid.sync import SyncJob

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
            for job in await self.bot.services.discord_sync.claim():
                await self._process_job(job)

    async def _process_job(self, job: SyncJob) -> None:
        try:
            # The same reconciler the bot exposes for latency nudges, so a command and
            # this job cannot render a resource two different ways.
            posts = self.bot.post_reconciler
            if posts.handles(job.resource_kind):
                # A delete needs no special case: the renderer reports a vanished
                # resource as "no posts wanted", and the diff loop removes them.
                await posts.reconcile(job.resource_kind, job.source_key, job.generation)
            elif job.action == "delete":
                await self._delete_projection(job)
            else:
                await self._refresh_vote(int(job.source_key))
                await self.bot.services.messages.mark_projection_applied(
                    job.resource_kind,
                    job.source_key,
                    job.generation,
                )
        except Exception as error:
            dead_lettered = await self.bot.services.discord_sync.fail(job, error)
            if dead_lettered:
                logger.exception(
                    "Dead-lettered Discord reconciliation job after repeated failures",
                    extra={"resource_kind": job.resource_kind, "source_key": job.source_key},
                )
            return
        await self.bot.services.discord_sync.complete(job)

    async def _delete_projection(self, job: SyncJob) -> None:
        """Delete retained Discord targets and their tracking rows idempotently.

        Only vote sessions still take this path; build posts are removed by the
        reconciler's diff loop.
        """
        targets = await self.bot.services.messages.list_projection(job.resource_kind, job.source_key)
        for target in targets:
            if target.channel_id is not None:
                message = await self.bot.get_or_fetch_message(target.channel_id, target.id)
                if message is not None:
                    with contextlib.suppress(discord.NotFound):
                        await message.delete()
            await self.bot.services.messages.untrack(target.id)

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
