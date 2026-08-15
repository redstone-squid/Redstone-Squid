"""Framework-neutral application services and process runtime."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import anyio
from anyio.abc import TaskGroup
from whenever import Instant

from squid.accounts.application import AccountService
from squid.artifacts import ArtifactStore
from squid.auth.application import ApiKeyService
from squid.auth.application.web import DiscordOAuthService
from squid.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.cli_auth import CliAuthorizationService
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.events.application import DomainEventService
from squid.events.infrastructure.listener import DomainEventWakeListener
from squid.idempotency import IdempotencyService
from squid.media.application.jobs import MediaNormalizationJobRunner, MediaNormalizationJobService, MediaStorageCleanup
from squid.messages.application import MessageService
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.notifications import NotificationService
from squid.observability import add_counter, record_gauge, record_histogram
from squid.permissions.application import (
    PermissionAdministrationService,
    PermissionEpochWatcher,
    PermissionService,
)
from squid.permissions.application.epoch import POLL_INTERVAL_SECONDS as PERMISSION_POLL_INTERVAL_SECONDS
from squid.records.application import RecordComputationService, RecordService
from squid.schematics.application import SchematicJobService, SchematicRenderJobService, SchematicService
from squid.search.application import SearchEmbeddingService, SearchService
from squid.settings.application import SettingsService
from squid.starboard.application import StarboardService
from squid.submissions.application import (
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFinalizationWorker,
    SubmissionFormService,
)
from squid.suggestions.application import SuggestionService
from squid.sync import DiscordSyncService
from squid.tags.application import TagService
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.application.ports import InteractiveVoteActorResolver

logger = logging.getLogger(__name__)

PERMISSION_EPOCH_JOB = "permission-epoch"
"""Name of the job every process runs to keep its permission cache current."""


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Capabilities exposed by the HTTP API process."""

    builds: BuildService
    api_keys: ApiKeyService | None
    web_auth: DiscordOAuthService | None
    cli_authorization: CliAuthorizationService | None
    idempotency: IdempotencyService
    notifications: NotificationService
    build_queries: BuildQueryService
    permissions: PermissionService
    permission_epoch: PermissionEpochWatcher
    records: RecordService
    schematics: SchematicService
    search: SearchService
    tags: TagService
    submission_forms: SubmissionFormService
    submission_drafts: SubmissionDraftService
    submission_finalization: SubmissionFinalizationService
    suggestions: SuggestionService
    media_jobs: MediaNormalizationJobService | None
    minecraft_installations: InstallationCredentialService | None
    minecraft_player_authorization: PlayerAuthorizationService | None
    accounts: AccountService
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
    permissions: PermissionService
    permission_admin: PermissionAdministrationService
    permission_epoch: PermissionEpochWatcher
    records: RecordService
    record_computation: RecordComputationService
    schematics: SchematicService
    search: SearchService
    tags: TagService
    settings: SettingsService
    starboards: StarboardService
    suggestions: SuggestionService
    accounts: AccountService
    versions: VersionService
    votes: VoteService
    discord_sync: DiscordSyncService
    domain_events: DomainEventService
    notifications: NotificationService
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
    event_wake_listener: DomainEventWakeListener | None
    notifications: NotificationService
    schematics: SchematicService
    schematic_jobs: SchematicJobService
    schematic_renders: SchematicRenderJobService
    media_runner: MediaNormalizationJobRunner | None
    media_cleanup: MediaStorageCleanup
    submission_finalization: SubmissionFinalizationWorker
    search_embeddings: SearchEmbeddingService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]
    record_queue_health: Callable[[], Awaitable[None]]
    purge_idempotency: Callable[[], Awaitable[int]]
    expire_submission_drafts: Callable[[], Awaitable[int]]


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


def start_permission_epoch_watch(
    supervisor: "BackgroundTaskSupervisor",
    watcher: PermissionEpochWatcher,
) -> None:
    """Start one process's permission-cache invalidation jobs.

    Two jobs, deliberately: the poll is what makes invalidation durable, and the
    `LISTEN` connection only shortens the window. A deployment with no listener
    URL configured still converges, just five seconds slower.
    """
    supervisor.start_periodic(
        watcher.refresh,
        name=PERMISSION_EPOCH_JOB,
        interval=PERMISSION_POLL_INTERVAL_SECONDS,
    )
    if watcher.listener is not None:
        supervisor.start(watcher.listen(), name=f"{PERMISSION_EPOCH_JOB}-listener")


@dataclass(eq=False, slots=True)
class JobHandle:
    """A supervised task, cancellable independently of its siblings.

    Task groups cancel as a unit, so per-job cancellation needs a scope of its
    own. The handle carries that scope plus a completion event, because
    ``start_soon`` returns nothing to await.
    """

    name: str
    scope: anyio.CancelScope
    finished: anyio.Event

    def cancel(self) -> None:
        """Ask this job to stop without waiting for it."""
        self.scope.cancel()


class BackgroundTaskSupervisor:
    """Own process background tasks and await every task during shutdown.

    The supervisor must be entered with ``async with supervisor.running()``
    before any work is started, and that ``async with`` has to live in the task
    that owns the process, since a task group can only be exited by the task
    that entered it.
    """

    def __init__(self, *, shutdown_timeout: float = 10.0) -> None:
        self._shutdown_timeout = shutdown_timeout
        self._task_group: TaskGroup | None = None
        self._handles: set[JobHandle] = set()
        self._closing = False
        self._last_success: dict[str, Instant] = {}

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[Self]:
        """Hold the task group that owns every supervised job."""
        if self._task_group is not None:
            msg = "The background task supervisor is already running."
            raise RuntimeError(msg)
        async with anyio.create_task_group() as task_group:
            self._task_group = task_group
            try:
                yield self
            finally:
                # Leaves the task group with no children, so its own exit cannot
                # block on work that close() was supposed to have stopped.
                await self.close()
                # close() gives up at the shutdown deadline, so re-cancel at the
                # group level to force down anything merely slow to notice.
                #
                # Unlike the asyncio.wait this replaces, a task group cannot
                # abandon a child: a job that shields itself past the deadline
                # will still delay process exit rather than being left orphaned.
                # That is the intended trade -- orphaned tasks are what the
                # supervisor exists to prevent -- and nothing here shields.
                task_group.cancel_scope.cancel()

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

    def start(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> JobHandle:
        """Start one owned task while this supervisor is accepting work."""
        if self._task_group is None:
            coroutine.close()
            msg = "Cannot start background work before the supervisor is running."
            raise RuntimeError(msg)
        if self._closing:
            coroutine.close()
            msg = "Cannot start background work while the supervisor is closing."
            raise RuntimeError(msg)
        handle = JobHandle(name=name, scope=anyio.CancelScope(), finished=anyio.Event())
        self._handles.add(handle)
        self._task_group.start_soon(self._run_owned, coroutine, handle, name=name)
        return handle

    def start_periodic(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
        interval: float,
        run_immediately: bool = True,
    ) -> JobHandle:
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
        await self._stop(tuple(self._handles), description="Background tasks")

    async def cancel(self, *handles: JobHandle) -> None:
        """Cancel and await a feature's owned tasks without closing the process supervisor."""
        await self._stop(handles, description="Feature background tasks")

    async def _stop(self, handles: tuple[JobHandle, ...], *, description: str) -> None:
        if not handles:
            return
        for handle in handles:
            handle.cancel()
        with anyio.move_on_after(self._shutdown_timeout):
            for handle in handles:
                await handle.finished.wait()
        pending = [handle.name for handle in handles if not handle.finished.is_set()]
        if pending:
            logger.error(
                "%s did not stop before the shutdown deadline",
                description,
                extra={"squid.tasks": len(pending), "squid.task.names": pending},
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
            await anyio.sleep(interval)
        while True:
            started = time.perf_counter()
            attributes = {"squid.job.name": name}
            try:
                await operation()
            except anyio.get_cancelled_exc_class():
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
            await anyio.sleep(interval)

    async def _run_owned(self, coroutine: Coroutine[Any, Any, None], handle: JobHandle) -> None:
        try:
            with handle.scope:
                await coroutine
        except Exception:
            # Failures stay local to the job. Letting one escape would cancel
            # every sibling in the task group, which for the worker means one
            # bad job taking down the other seventeen.
            if not self._closing:
                logger.exception("Background task %s stopped unexpectedly", handle.name)
        finally:
            self._handles.discard(handle)
            handle.finished.set()
