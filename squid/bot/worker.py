"""Dispatch custom events in the Redstone Squid system to the bot"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import asyncpg
from discord.ext import tasks
from discord.ext.commands import Cog
from sqlalchemy import func, select, update
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from squid.db.schema import Event

if TYPE_CHECKING:
    import squid.bot


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class EventConfig:
    channel_name: str = "domain_events"
    max_concurrent_events: int = 10
    queue_size: int = 1000
    queue_timeout: float = 5.0


class CustomEventCog[BotT: "squid.bot.RedstoneSquid"](Cog):
    """Cog to handle custom events using PostgreSQL LISTEN/NOTIFY."""

    def __init__(
        self,
        bot: BotT,
        config: EventConfig = EventConfig(),
    ):
        self.bot = bot
        self.channel_name = config.channel_name
        self.max_concurrent_events = config.max_concurrent_events
        self._tasks: set[asyncio.Task[Any]] = set()  # Keep a reference to background tasks
        self.processing_semaphore = asyncio.Semaphore(config.max_concurrent_events)
        self.queue_size = config.queue_size
        self.queue_timeout = config.queue_timeout
        self.pg_listener.start()

    @override
    async def cog_unload(self) -> None:
        self.pg_listener.stop()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _process_event(self, event_id: int) -> None:
        """Atomically process an event."""
        try:
            async with self.bot.db.async_session() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Event)
                        .where(Event.id == event_id, Event.processed_at.is_(None))
                        .with_for_update(skip_locked=True)
                    )
                    event = result.scalar_one_or_none()

                    if event:
                        # The dispatching is asynchronous, so there is no guarantee we actually successfully processed the event.
                        # So this is a best-effort approach, if it fails then it fails.
                        self.bot.dispatch("squid_" + event.type, event)

                    await session.execute(update(Event).where(Event.id == event_id).values(processed_at=func.now()))
        except Exception as e:
            logger.error("Failed to process event %s: %s", event_id, e, exc_info=True)

    async def _process_event_with_semaphore(self, event_id: int) -> None:
        """Process an event with a semaphore to limit concurrency."""
        async with self.processing_semaphore:
            await self._process_event(event_id)

    async def _replay_backlog(self) -> None:
        """Handle every un-processed row before we begin LISTEN/NOTIFY."""
        async with self.bot.db.async_session() as session:
            result = await session.execute(select(Event).where(Event.processed_at.is_(None)).order_by(Event.id))
            events = result.scalars().all()

        logger.info("Replaying %s events from backlog", len(events))

        for event in events:
            try:
                await self._process_event(event.id)
            except Exception as e:
                logger.error("Failed to replay event %s: %s", event.id, e, exc_info=True)
                continue

    @tasks.loop(count=1)  # run once; the inner loop lives forever
    @retry(wait=wait_exponential_jitter(max=60), stop=stop_after_attempt(20))
    async def pg_listener(self) -> None:
        """LISTEN domain_events via asyncpg and dispatch events to the bot.

        This method additionally replays any unprocessed events from the database.
        """
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=self.queue_size)

        async def _on_notify(_conn: Any, _pid: int, _chan: str, payload: str):
            try:
                event_id = int(payload)
            except ValueError:
                logger.error("Invalid payload received: %s", payload)
                return

            try:
                await asyncio.wait_for(queue.put(event_id), timeout=self.queue_timeout)
            except asyncio.TimeoutError:
                logger.error(
                    "Event queue still full after waiting for %s seconds, dropping event %s",
                    self.queue_timeout,
                    event_id,
                )

        try:
            # keep one raw DBAPI connection out of the pool for LISTEN/NOTIFY
            raw = await self.bot.db.async_engine.raw_connection()
            driver = raw.driver_connection  # real asyncpg conn
            if not isinstance(driver, asyncpg.Connection):
                raise TypeError(f"Expected asyncpg.Connection, got {type(driver)}")

            # START LISTENING FIRST - this ensures no events are lost
            await driver.add_listener(self.channel_name, _on_notify)
            logger.info("Started listening for domain events")

            await self._replay_backlog()
            logger.info("Backlog replay completed")

            while True:
                event_id = await queue.get()
                task = asyncio.create_task(self._process_event_with_semaphore(event_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        finally:  # clean shutdown on cog unload
            await driver.remove_listener(self.channel_name, _on_notify)
            await raw.close()


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""

    cog = CustomEventCog(bot)
    await bot.add_cog(cog)
