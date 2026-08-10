"""Framework-neutral application services and process runtime."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Coroutine
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.auth.application import ApiKeyService
from squid.auth.application.web import DiscordOAuthService
from squid.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.events.application import DomainEventService
from squid.messages.application import MessageService
from squid.observability import add_counter, record_gauge, record_histogram
from squid.permissions.application import AuthorizationService
from squid.records.application import RecordComputationService, RecordService
from squid.schematics.application import SchematicJobService, SchematicRenderJobService, SchematicService
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
class ApiServices:
    """Capabilities exposed by the HTTP API process."""

    builds: BuildService
    api_keys: ApiKeyService | None
    web_auth: DiscordOAuthService | None
    build_queries: BuildQueryService
    authorization: AuthorizationService
    records: RecordService
    schematics: SchematicService
    search: SearchService
    tags: TagService
    users: UserService
    versions: VersionService
    votes: VoteService
    vote_members: InteractiveVoteActorResolver | None


@dataclass(frozen=True, slots=True)
class BotServices:
    """Capabilities exposed to Discord gateway features."""

    builds: BuildService
    build_inference: BuildInferenceService
    restrictions: RestrictionService
    build_queries: BuildQueryService
    messages: MessageService
    authorization: AuthorizationService
    records: RecordService
    record_computation: RecordComputationService
    schematics: SchematicService
    search: SearchService
    tags: TagService
    settings: SettingsService
    starboards: StarboardService
    users: UserService
    versions: VersionService
    votes: VoteService
    discord_sync: DiscordSyncService
    domain_events: DomainEventService
    redstoner: RedstonerService
    welcome_relay: WelcomeRelayService


@dataclass(frozen=True, slots=True)
class WorkerServices:
    """Capabilities exposed to transport-neutral background jobs."""

    builds: BuildService
    artifacts: ArtifactStore
    votes: VoteService
    records: RecordComputationService
    events: DomainEventService
    schematics: SchematicService
    schematic_jobs: SchematicJobService
    schematic_renders: SchematicRenderJobService
    search_embeddings: SearchEmbeddingService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]
    record_queue_health: Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApplicationRuntime[ServicesT]:
    """Own application services and process-level resource callbacks."""

    services: ServicesT
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

    def is_healthy(self, required: Collection[str], *, max_age_seconds: float) -> bool:
        """Return whether every required job completed successfully within the allowed age."""
        if max_age_seconds <= 0:
            msg = "Background heartbeat age must be positive."
            raise ValueError(msg)
        now = Instant.now()
        return all(
            (last_success := self._last_success.get(name)) is not None
            and (now - last_success).total("seconds") <= max_age_seconds
            for name in required
        )

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
        done, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if pending:
            logger.error(
                "Background tasks did not stop before the shutdown deadline", extra={"squid.tasks": len(pending)}
            )

    async def cancel(self, *tasks: asyncio.Task[Any]) -> None:
        """Cancel and await a feature's owned tasks without closing the process supervisor."""
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if pending:
                logger.error(
                    "Feature background tasks did not stop before the shutdown deadline",
                    extra={"squid.tasks": len(pending)},
                )

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
            started = time.perf_counter()
            attributes = {"squid.job.name": name}
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background job %s failed", name)
                add_counter("squid.background.job.runs", attributes={**attributes, "squid.outcome": "error"})
                record_histogram(
                    "squid.background.job.duration",
                    time.perf_counter() - started,
                    attributes={**attributes, "squid.outcome": "error"},
                )
            else:
                succeeded_at = Instant.now()
                self._last_success[name] = succeeded_at
                add_counter("squid.background.job.runs", attributes={**attributes, "squid.outcome": "ok"})
                record_histogram(
                    "squid.background.job.duration",
                    time.perf_counter() - started,
                    attributes={**attributes, "squid.outcome": "ok"},
                )
                record_gauge("squid.background.job.last_success", time.time(), attributes=attributes)
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
