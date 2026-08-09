"""Framework-neutral application services and process runtime."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from whenever import Instant

from squid.auth.application import ApiKeyService
from squid.auth.application.web import DiscordOAuthService
from squid.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.events.application import DomainEventService
from squid.messages.application import MessageService
from squid.permissions.application import AuthorizationService
from squid.records.application import RecordComputationService, RecordService
from squid.schematics.application import SchematicJobService, SchematicService
from squid.search.application import SearchEmbeddingService, SearchService
from squid.settings.application import SettingsService
from squid.starboard.application import StarboardService
from squid.sync import DiscordSyncService
from squid.tags.application import TagService
from squid.users.application import UserService
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.application.ports import InteractiveVoteActorResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Long-lived application services created by the process composition root."""

    builds: BuildService
    api_keys: ApiKeyService | None
    web_auth: DiscordOAuthService | None
    build_inference: BuildInferenceService
    restrictions: RestrictionService
    build_queries: BuildQueryService
    messages: MessageService
    authorization: AuthorizationService
    records: RecordService
    record_computation: RecordComputationService
    schematics: SchematicService
    schematic_jobs: SchematicJobService
    search: SearchService
    search_embeddings: SearchEmbeddingService
    tags: TagService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]
    settings: SettingsService
    starboards: StarboardService
    users: UserService
    versions: VersionService
    votes: VoteService
    vote_members: InteractiveVoteActorResolver | None
    discord_sync: DiscordSyncService
    domain_events: DomainEventService
    redstoner: RedstonerService
    welcome_relay: WelcomeRelayService


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Own application services and process-level resource callbacks."""

    services: ApplicationServices
    close_resources: Callable[[], Awaitable[None]]
    keep_database_active: Callable[[], Awaitable[None]]
    check_readiness: Callable[[], Awaitable[None]] | None = None

    async def close(self) -> None:
        await self.close_resources()

    async def ready(self) -> None:
        """Raise when a required process dependency is unavailable."""
        check = self.check_readiness or self.keep_database_active
        await check()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


class BackgroundTaskSupervisor:
    """Own process background tasks and await every task during shutdown."""

    def __init__(self, *, shutdown_timeout: float = 10.0) -> None:
        self._shutdown_timeout = shutdown_timeout
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._last_success: dict[str, Instant] = {}

    @property
    def last_success(self) -> dict[str, Instant]:
        """Return a snapshot of successful periodic-job heartbeats."""
        return dict(self._last_success)

    def start(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task[None]:
        """Start one owned task while this supervisor is accepting work."""
        if self._closing:
            coroutine.close()
            msg = "Cannot start background work while the supervisor is closing."
            raise RuntimeError(msg)
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def start_periodic(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
        interval: float,
        run_immediately: bool = True,
    ) -> asyncio.Task[None]:
        """Run an operation repeatedly with uniform failure isolation and ownership."""
        if interval <= 0:
            msg = "Periodic job interval must be positive."
            raise ValueError(msg)
        return self.start(
            self._run_periodic(operation, name=name, interval=interval, run_immediately=run_immediately),
            name=name,
        )

    async def close(self) -> None:
        """Stop accepting work, cancel tasks, and wait for all of them with a deadline."""
        if self._closing:
            return
        self._closing = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.exception("Background tasks did not stop before the shutdown deadline")

    async def _run_periodic(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
        interval: float,
        run_immediately: bool,
    ) -> None:
        if not run_immediately:
            await asyncio.sleep(interval)
        while True:
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background job %s failed", name)
            else:
                self._last_success[name] = Instant.now()
            await asyncio.sleep(interval)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if self._closing or task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Background task %s stopped unexpectedly",
                task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )
