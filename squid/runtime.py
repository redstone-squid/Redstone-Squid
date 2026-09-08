"""Application services and process runtime, defined without reference to a web framework."""

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
from squid.auth.application.web import WebSessionService
from squid.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.cli_auth import CliAuthorizationService
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.log_capture import captured, install_log_capture
from squid.events.application import DomainEventService
from squid.events.infrastructure.listener import DomainEventWakeListener
from squid.idempotency import IdempotencyService
from squid.media.application.jobs import MediaNormalizationJobRunner, MediaNormalizationJobService, MediaStorageCleanup
from squid.messages.application import MessageService
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.notifications import NotificationService
from squid.observability import (
    add_counter,
    correlated_log_buffer,
    correlation_reference,
    correlation_scope,
    record_gauge,
    record_histogram,
)
from squid.permissions.application import (
    PermissionAdministrationService,
    PermissionEpochWatcher,
    PermissionService,
)
from squid.permissions.application.epoch import POLL_INTERVAL_SECONDS as PERMISSION_POLL_INTERVAL_SECONDS
from squid.posts.application import PostService
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
from squid.sync import DiscordReconciliationService
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
    error_reports: ErrorReportService
    api_keys: ApiKeyService | None
    web_auth: WebSessionService | None
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
    error_reports: ErrorReportService
    build_inference: BuildInferenceService
    restrictions: RestrictionService
    build_queries: BuildQueryService
    messages: MessageService
    posts: PostService
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
    discord_reconciliation: DiscordReconciliationService
    domain_events: DomainEventService
    notifications: NotificationService
    redstoner: RedstonerService
    welcome_relay: WelcomeRelayService


@dataclass(frozen=True, slots=True)
class WorkerServices:
    """Capabilities exposed to background jobs, which serve no request."""

    builds: BuildService
    error_reports: ErrorReportService
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
    """Own one process's services; `close` unwinds the resource stack behind them, in LIFO order."""

    services: ServicesT
    close_resources: Callable[[], Awaitable[None]]
    keep_database_active: Callable[[], Awaitable[None]]
    check_readiness: Callable[[], Awaitable[None]] | None = None

    async def close(self) -> None:
        await self.close_resources()

    async def ready(self) -> None:
        """Raise whatever the readiness check raises when a required dependency is unavailable.

        Falls back to the database keepalive when the runtime was built without a dedicated check.
        """
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


LOG_CAPTURE_JOB = "error-log-capture"
"""Name of the job that stores logged exceptions as error reports."""


def start_log_capture(
    supervisor: BackgroundTaskSupervisor,
    service: ErrorReportService,
    *,
    enabled: bool = True,
    capacity: int = 256,
) -> None:
    """Store exceptions logged at ERROR or above as reports, under `LOG_CAPTURE_JOB` on `supervisor`.

    This is what makes worker failures reachable at all: its queue consumers absorb a failure,
    dead-letter the job and log it, so nothing reaches the supervisor that would have captured it.
    `enabled=False` starts nothing; `capacity` bounds the failures held between the logging call
    and the database write, and the oldest are dropped past it.
    """
    if not enabled:
        return
    handler = install_log_capture(capacity=capacity)
    supervisor.start(handler.run(service), name=LOG_CAPTURE_JOB)


def start_permission_epoch_watch(
    supervisor: BackgroundTaskSupervisor,
    watcher: PermissionEpochWatcher,
) -> None:
    """Start one process's permission-cache invalidation jobs.

    Two jobs, deliberately: the poll is what makes invalidation durable, and the `LISTEN`
    connection only shortens the window. A deployment with no listener URL still converges,
    just a poll interval slower.
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
    """A supervised task, whose `cancel` stops it without touching its siblings.

    Task groups cancel as a unit, so per-job cancellation needs a scope of its own; `finished`
    exists because `start_soon` returns nothing to await.
    """

    name: str
    scope: anyio.CancelScope
    finished: anyio.Event

    def cancel(self) -> None:
        """Ask this job to stop without waiting for it."""
        self.scope.cancel()


class BackgroundTaskSupervisor:
    """Owner of every background task in the process; `close` cancels them and awaits them all.

    Work can only start inside ``async with supervisor.running()``, which holds the task group and
    must be entered by the task that owns the process, since a task group can only be exited by
    the task that entered it. Leaving that block closes the supervisor.
    """

    def __init__(self, *, shutdown_timeout: float = 10.0) -> None:
        self._shutdown_timeout = shutdown_timeout
        self._task_group: TaskGroup | None = None
        self._handles: set[JobHandle] = set()
        self._closing = False
        self._last_success: dict[str, Instant] = {}
        self._error_reports: ErrorReportService | None = None

    def capture_failures_into(self, service: ErrorReportService | None) -> None:
        """Store background job failures, which otherwise only ever reach a log file.

        Set after construction because the supervisor is built before the service graph; a job
        that fails before this is called is still logged.
        """
        self._error_reports = service

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[Self]:
        """Hold the task group that owns every supervised job, closing the supervisor on exit.

        Raises:
            RuntimeError: This supervisor is already running.
        """
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
                # group level to force down anything merely slow to notice. A task
                # group cannot abandon a child, so a job that shielded itself past
                # the deadline would delay process exit rather than be orphaned;
                # that is the intended trade, and nothing here shields.
                task_group.cancel_scope.cancel()

    @property
    def last_success(self) -> dict[str, Instant]:
        """Return a snapshot of successful periodic-job heartbeats."""
        return dict(self._last_success)

    def is_healthy(self, required: Collection[str], *, max_age_seconds: float) -> bool:
        """Return whether every required job completed successfully within the allowed age.

        A job that has never succeeded, including one that has not yet run, counts as unhealthy.

        Raises:
            ValueError: `max_age_seconds` is not positive.
        """
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
        """Start one owned task, whose failures stay local to it and never cancel a sibling.

        Closes `coroutine` rather than leaving it un-awaited when the call is refused.

        Raises:
            RuntimeError: The supervisor is not running yet, or is already closing.
        """
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
        """Run an operation every `interval` seconds until cancelled, each run its own correlation.

        A failing run is captured, logged and counted, then the loop waits and runs again; only
        the successful runs advance the job's `last_success` heartbeat.

        Raises:
            ValueError: `interval` is not positive.
            RuntimeError: The supervisor is not running yet, or is already closing.
        """
        if interval <= 0:
            msg = "Periodic job interval must be positive."
            raise ValueError(msg)
        return self.start(
            self._run_periodic(operation, name=name, interval=interval, run_immediately=run_immediately),
            name=name,
        )

    async def close(self) -> None:
        """Stop accepting work, cancel every task, and await them until `shutdown_timeout`.

        Tasks still running at the deadline are logged by name and left; calling twice is a no-op.
        """
        if self._closing:
            return
        self._closing = True
        await self._stop(tuple(self._handles), description="Background tasks")

    async def cancel(self, *handles: JobHandle) -> None:
        """Cancel and await a feature's own tasks, under the same deadline, leaving the rest alone."""
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
            # One correlation per run, so a failure's stored report carries the lines this run
            # logged rather than a slice of whatever else the process was doing. The logging call
            # stays inside the scope, or it would not carry the id the report is filed under.
            with correlation_scope() as correlation:
                try:
                    await operation()
                except anyio.get_cancelled_exc_class():
                    raise
                except Exception as error:
                    # Captured before logging, so the tail is the run rather than an echo of the
                    # traceback the report already holds.
                    await self._capture(error, name=name, correlation=correlation)
                    logger.exception(
                        "Background job %s failed",
                        name,
                        extra={"squid.job.error_id": correlation, **captured()},
                    )
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

    async def _capture(self, error: BaseException, *, name: str, correlation: str) -> None:
        """Store one background job failure, never letting the attempt kill the loop."""
        if self._error_reports is None:
            return
        try:
            buffer = correlated_log_buffer()
            await self._error_reports.record(
                error,
                correlation_id=correlation,
                reference=correlation_reference(correlation),
                surface="background_job",
                origin=name,
                context={"job": name},
                log_tail=buffer.drain(correlation) if buffer is not None else (),
            )
        except Exception:
            logger.exception("Could not capture a background job failure [job=%s]", name)

    async def _run_owned(self, coroutine: Coroutine[Any, Any, None], handle: JobHandle) -> None:
        try:
            with handle.scope:
                await coroutine
        except Exception:
            # Failures stay local to the job: letting one escape would cancel every
            # sibling in the task group, so one bad job would take down the worker.
            if not self._closing:
                logger.exception("Background task %s stopped unexpectedly", handle.name)
        finally:
            self._handles.discard(handle)
            handle.finished.set()
