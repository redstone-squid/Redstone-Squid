"""Drain durable Discord and search-projection reconciliation work."""

import contextlib
import logging
from typing import TYPE_CHECKING, override

import discord
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
            dropped = await self.bot.services.discord_sync.fail(job, error)
            if dropped:
                logger.exception(
                    "Dropped Discord reconciliation job after repeated failures",
                    extra={"resource_kind": job.resource_kind, "source_key": job.source_key},
                )
            return
        await self.bot.services.discord_sync.complete(job)

    async def _refresh_build(self, build_id: int) -> None:
        build = await self.bot.services.build_queries.get(build_id)
        if build is not None:
            await self.bot.for_build(build).update_messages()

    async def _refresh_vote(self, vote_session_id: int) -> None:
        snapshot = await self.bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None:
            return
        if snapshot.kind == "build":
            build_id = snapshot.target.build_id
            if build_id is not None and snapshot.status == "closed":
                if snapshot.result == "approved":
                    await self.bot.services.builds.confirm(build_id)
                elif snapshot.result == "denied":
                    await self.bot.services.builds.deny(build_id)
            session = await BuildVoteSession.from_id(self.bot, vote_session_id)
        elif snapshot.kind == "delete_log":
            session = await DeleteLogVoteSession.from_id(self.bot, vote_session_id)
            if session is not None and snapshot.status == "closed" and snapshot.result == "approved":
                with contextlib.suppress(discord.NotFound):
                    await session.target_message.delete()
        else:
            session = await GenericVoteSession.from_id(self.bot, vote_session_id)
        if session is not None:
            await session.update_messages()

    @process_reconciliation.before_loop
    async def before_process_reconciliation(self) -> None:
        await self.bot.wait_until_ready()
