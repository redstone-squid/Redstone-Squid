"""Provide application services and manage work that lasts for a process's lifetime.

Each process receives a service bundle for its role: HTTP API, Discord bot, or
background worker. ApplicationRuntime pairs that bundle with dependency checks
and resource cleanup. BackgroundTaskSupervisor owns ongoing jobs so shutdown
can cancel them and wait for their cleanup before the process exits.
"""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any, Self

import anyio
import discord
from anyio.abc import TaskGroup
from whenever import Instant

from squid.core.accounts.application import AccountService
from squid.core.artifacts import ArtifactStore
from squid.core.auth.application import ApiKeyService
from squid.core.auth.application.web import WebSessionService
from squid.core.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.core.builds.application.ports import BuildSchematicSummaryProvider
from squid.core.cli_auth import CliAuthorizationService
from squid.core.community.application import WelcomeRelayService
from squid.core.diagnostics.application import ErrorReportService
from squid.core.diagnostics.log_capture import captured, install_log_capture
from squid.core.edits.application import EditRunner
from squid.core.events.application import DomainEventService
from squid.core.events.infrastructure.listener import DomainEventWakeListener
from squid.core.events.models import DomainEvent
from squid.core.idempotency import IdempotencyService
from squid.core.media.application.jobs import MediaProcessingJobRunner, MediaProcessingJobService, MediaStorageCleanup
from squid.core.media.infrastructure.build_media import PublishedBuildMedia
from squid.core.messages.application import MessageService
from squid.core.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.core.notifications import NotificationService
from squid.core.observability import (
    add_counter,
    correlated_log_buffer,
    correlation_reference,
    correlation_scope,
    record_gauge,
    record_histogram,
)
from squid.core.permissions.application import (
    PermissionAdministrationService,
    PermissionEpochWatcher,
    PermissionService,
)
from squid.core.permissions.application.epoch import POLL_INTERVAL_SECONDS as PERMISSION_POLL_INTERVAL_SECONDS
from squid.core.posts.application import PostService
from squid.core.records.application import PublicRecordQueryService, RecordComputationService, RecordService
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.schematics.application import SchematicJobService, SchematicRenderJobService, SchematicService
from squid.core.search.application import SearchEmbeddingService, SearchService
from squid.core.settings.application import SettingsService
from squid.core.starboard.application import StarboardService
from squid.core.submissions.application import (
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFinalizationWorker,
    SubmissionFormService,
)
from squid.core.submissions.application.attachment_uploads import DraftAttachmentUploads
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.core.submissions.application.intake import SubmissionAttachmentIntake
from squid.core.submissions.application.recalculation import RecalculationProposalService
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.suggestions.application import SuggestionService
from squid.core.sync import DiscordReconciliationService
from squid.core.tags.application import TagService
from squid.core.versions.application.services import VersionService
from squid.core.voting.application import VoteService
from squid.core.voting.application.ports import InteractiveVoteActorResolver
from squid.core.voting.application.recount import BallotRecounter, VoteCardReactions
from squid_reactivity import TopicBus
from squid_storage import PostgresTopicBridge

logger = logging.getLogger(__name__)

type TopicBridgeFactory = Callable[[TopicBus], Awaitable[PostgresTopicBridge | None]]


class RuntimeJob(StrEnum):
    """Stable process job names; resource-specific jobs may use dynamic names."""

    PERMISSION_EPOCH = "permission-epoch"
    PERMISSION_LISTENER = "permission-epoch-listener"
    LOG_CAPTURE = "error-log-capture"
    LAYOUT = "layout-runtime"
    INTERACTION_METRICS = "interaction-metrics"
    TOPIC_BRIDGE = "layout-topic-bridge"


PERMISSION_EPOCH_JOB = RuntimeJob.PERMISSION_EPOCH
"""Name of the job every process runs to keep its permission cache current."""


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Capabilities exposed by the HTTP API process."""

    settings: SettingsService
    edits: EditRunner

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
    public_records: PublicRecordQueryService
    range_publication: RangePublicationService
    schematics: SchematicService
    search: SearchService
    tags: TagService
    submission_forms: SubmissionFormService
    submission_drafts: SubmissionDraftService
    submission_finalization: SubmissionFinalizationService
    submission_schematics: DraftSchematicService
    submission_recalculations: RecalculationProposalService
    submission_intake: SubmissionAttachmentIntake
    submission_inference: SubmissionInferenceRuns
    suggestions: SuggestionService
    media_jobs: MediaProcessingJobService | None
    minecraft_installations: InstallationCredentialService | None
    minecraft_player_authorization: PlayerAuthorizationService | None
    accounts: AccountService
    versions: VersionService
    votes: VoteService
    vote_members: InteractiveVoteActorResolver | None
    published_build_media: PublishedBuildMedia | None = None


@dataclass(frozen=True, slots=True)
class BotServices:
    """Capabilities exposed to Discord gateway features."""

    builds: BuildService
    error_reports: ErrorReportService
    build_inference: BuildInferenceService
    submission_forms: SubmissionFormService
    submission_drafts: SubmissionDraftService
    submission_finalization: SubmissionFinalizationService
    submission_schematics: DraftSchematicService
    submission_recalculations: RecalculationProposalService
    submission_uploads: DraftAttachmentUploads
    submission_intake: SubmissionAttachmentIntake
    submission_inference: SubmissionInferenceRuns
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
    edits: EditRunner
    starboards: StarboardService
    suggestions: SuggestionService
    accounts: AccountService
    versions: VersionService
    votes: VoteService
    vote_cards: Callable[[discord.Client], VoteCardReactions]
    """Build the vote card adapter for the bot's own client, which bootstrap cannot see."""
    discord_reconciliation: DiscordReconciliationService
    domain_events: DomainEventService
    notifications: NotificationService
    welcome_relay: WelcomeRelayService
    schematic_summaries: BuildSchematicSummaryProvider | None = None
    published_build_media: PublishedBuildMedia | None = None
    open_topic_bridge: TopicBridgeFactory | None = None


@dataclass(frozen=True, slots=True)
class WorkerServices:
    """Capabilities exposed to background jobs, which serve no request."""

    builds: BuildService
    error_reports: ErrorReportService
    artifacts: ArtifactStore
    votes: VoteService
    ballot_recounter: BallotRecounter | None
    """Recounts a poll from its cards before the deadline closes it; None without a Discord token."""
    range_publication: RangePublicationService
    records: RecordComputationService
    events: DomainEventService
    event_wake_listener: DomainEventWakeListener | None
    notifications: NotificationService
    schematics: SchematicService
    schematic_jobs: SchematicJobService
    schematic_renders: SchematicRenderJobService
    media_runner: MediaProcessingJobRunner | None
    media_cleanup: MediaStorageCleanup
    submission_finalization: SubmissionFinalizationWorker
    index_build_embedding: Callable[[DomainEvent], Awaitable[None]]
    search_embeddings: SearchEmbeddingService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]
    record_queue_health: Callable[[], Awaitable[None]]
    purge_idempotency: Callable[[], Awaitable[int]]
    expire_submission_drafts: Callable[[], Awaitable[int]]
    process_submission_schematics: Callable[[], Awaitable[None]]
    cleanup_submission_schematics: Callable[[], Awaitable[None]]
    cleanup_submission_inference: Callable[[], Awaitable[None]]
    open_topic_bridge: TopicBridgeFactory | None = None


@dataclass(frozen=True, slots=True)
class ApplicationRuntime[ServicesT]:
    """Keep a process's services together with their readiness and cleanup callbacks.

    Composition code creates the services and supplies the callbacks. Use this
    runtime as an async context manager to close those resources on exit; call
    ready separately when checking whether the process can serve work.
    """

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


LOG_CAPTURE_JOB = RuntimeJob.LOG_CAPTURE
"""Name of the job that stores logged exceptions as error reports."""


def start_log_capture(
    supervisor: BackgroundTaskSupervisor,
    service: ErrorReportService,
    *,
    enabled: bool = True,
    capacity: int = 256,
) -> None:
    """Start collecting logged exceptions as error reports when enabled.

    Queue workers often catch failures and log them before retrying or stopping
    a job. Log capture makes those failures available even though they never
    reach the supervisor as exceptions.
    """
    if not enabled:
        return
    handler = install_log_capture(capacity=capacity)
    supervisor.start(handler.run(service), name=LOG_CAPTURE_JOB)


def start_permission_epoch_watch(
    supervisor: BackgroundTaskSupervisor,
    watcher: PermissionEpochWatcher,
) -> None:
    """Start polling for permission changes and listen for wake hints when configured.

    Polling keeps the cache current even if PostgreSQL notifications are missed.
    The optional listener lets the process notice changes sooner.
    """
    supervisor.start_periodic(
        watcher.refresh,
        name=PERMISSION_EPOCH_JOB,
        interval=PERMISSION_POLL_INTERVAL_SECONDS,
    )
    if watcher.listener is not None:
        supervisor.start(watcher.listen(), name=RuntimeJob.PERMISSION_LISTENER)


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
        self._error_reports: ErrorReportService | None = None

    def capture_failures_into(self, service: ErrorReportService | None) -> None:
        """Choose where periodic jobs store error reports, or disable capture with `None`.

        This can be set after the application services have been created. Failures
        are still logged when no report service is configured.
        """
        self._error_reports = service

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
                await self.close()
                # Cancel the group too if individual jobs missed the deadline.
                # Exiting still waits for every child, including shielded work.
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
        """Start a job and return a handle for cancellation and completion.

        Raise `RuntimeError` and close the supplied coroutine if the supervisor has
        not started or is closing. A job failure is logged without cancelling siblings.
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
        """Run an operation repeatedly, waiting `interval` seconds after each run.

        Failures are logged and the next run is still scheduled. Successful runs
        update the heartbeat under `name`. By default the first run starts at once;
        set `run_immediately=False` to wait one interval first.
        """
        if interval <= 0:
            msg = "Periodic job interval must be positive."
            raise ValueError(msg)
        return self.start(
            self._run_periodic(operation, name=name, interval=interval, run_immediately=run_immediately),
            name=name,
        )

    async def close(self) -> None:
        """Stop accepting work, cancel jobs, and wait up to the shutdown timeout.

        Log jobs that miss the deadline. Leaving `running()` still waits for the task
        group, so a job that shields cancellation can delay process exit.
        """
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
            # One correlation per run, so a failure's stored report carries the lines this run
            # logged rather than a slice of whatever else the process was doing. The logging call
            # stays inside the scope; only then does it carry the id the report is filed under.
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
        """Store a periodic job failure and log any error from the reporting service."""
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
            # Letting a failure escape would cancel every sibling in the task group.
            if not self._closing:
                logger.exception("Background task %s stopped unexpectedly", handle.name)
        finally:
            self._handles.discard(handle)
            handle.finished.set()
