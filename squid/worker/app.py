"""Database maintenance worker and process entry point."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
from whenever import Instant

from squid.bootstrap import create_worker_runtime
from squid.config import WorkerConfig, WorkerProcessConfig, load_or_exit, load_worker_process_config
from squid.health import ProcessHealthServer
from squid.logging_config import configure_service_worker_logging
from squid.observability import configure_observability, record_histogram, trace_span
from squid.runtime import BackgroundTaskSupervisor, WorkerServices, start_log_capture
from squid.schematics.infrastructure.capability import NullSchematicAnalyzer, engine_installed
from squid.schematics.infrastructure.durable import SchematicJobRunner
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.topics import open_topic_bridge
from squid.worker.events import ApplyBuildVoteOutcomeHandler, CoreDomainEventRunner, MaterializeNotificationHandler
from squid.worker.rendering import SchematicRenderProjector
from squid_reactivity import LocalTopicBus

logger = logging.getLogger(__name__)


class DatabaseWorker:
    """Own periodic database-only application work."""

    def __init__(
        self,
        services: WorkerServices,
        keep_database_active: Callable[[], Awaitable[None]],
        config: WorkerConfig,
        schematic_jobs: SchematicJobRunner,
        schematic_renders: SchematicRenderProjector,
        *,
        supervisor: BackgroundTaskSupervisor | None = None,
        schematic_pool: SchematicWorkerPool | None = None,
    ) -> None:
        self._services = services
        self._keep_database_active = keep_database_active
        self._config = config
        self._schematic_jobs = schematic_jobs
        self._schematic_renders = schematic_renders
        self._schematic_pool = schematic_pool
        self._supervisor = supervisor or BackgroundTaskSupervisor()
        self._supervisor.capture_failures_into(services.error_reports)
        self._event_lock = asyncio.Lock()
        outcome_handler = ApplyBuildVoteOutcomeHandler(services.votes, services.builds)
        notification_handler = MaterializeNotificationHandler(services.notifications)
        self._events = CoreDomainEventRunner(
            services.events,
            {
                "build.submitted": (notification_handler,),
                "build.confirmed": (notification_handler,),
                "build.denied": (notification_handler,),
                "record_run.activated": (notification_handler,),
                "vote_session.closed": (outcome_handler,),
            },
        )

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        """Hold the task group that owns every worker job, for the lifetime of the process."""
        async with self._supervisor.running():
            yield

    def start(self) -> None:
        """Start all jobs after the runtime has been constructed successfully."""
        event_interval = self._config.event_interval_seconds
        maintenance_interval = self._config.maintenance_interval_seconds
        self._supervisor.start_periodic(
            self._process_events,
            name="core-domain-events",
            interval=event_interval,
        )
        if self._services.event_wake_listener is not None:
            self._supervisor.start(
                self._services.event_wake_listener.run(self._process_events),
                name="domain-event-listener",
            )
        self._supervisor.start_periodic(
            self._process_schematic_jobs,
            name="schematic-jobs",
            interval=self._config.schematic_job_interval_seconds,
        )
        self._supervisor.start_periodic(
            self._process_schematic_renders,
            name="schematic-renders",
            interval=self._config.schematic_job_interval_seconds,
        )
        if self._services.media_runner is not None:
            self._supervisor.start_periodic(
                self._process_media_jobs,
                name="media-normalization",
                interval=self._config.media_job_interval_seconds,
            )
        self._supervisor.start_periodic(
            self._cleanup_media_storage,
            name="media-storage-cleanup",
            interval=self._config.media_cleanup_interval_seconds,
        )
        self._supervisor.start_periodic(
            self._process_submission_finalization,
            name="submission-finalization",
            interval=self._config.submission_finalization_interval_seconds,
        )
        self._supervisor.start_periodic(
            self._refresh_search,
            name="search-projections",
            interval=event_interval,
        )
        self._supervisor.start_periodic(
            self._process_search_embeddings,
            name="search-embeddings",
            interval=event_interval,
        )
        self._supervisor.start_periodic(
            self._process_records,
            name="record-maintenance",
            interval=maintenance_interval,
        )
        self._supervisor.start_periodic(
            self._close_due_votes,
            name="due-votes",
            interval=maintenance_interval,
        )
        self._supervisor.start_periodic(
            self._clean_stale_build_locks,
            name="stale-build-locks",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._cleanup_schematic_jobs,
            name="schematic-job-cleanup",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._services.record_queue_health,
            name="queue-health",
            interval=maintenance_interval,
        )
        if self._schematic_pool is not None:
            self._supervisor.start_periodic(
                self._schematic_pool.record_health,
                name="schematic-pool-health",
                interval=maintenance_interval,
            )
        self._supervisor.start_periodic(
            self._cleanup_notifications,
            name="notification-retention",
            interval=max(maintenance_interval, 3600),
        )
        self._supervisor.start_periodic(
            self._cleanup_idempotency,
            name="idempotency-retention",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._expire_submission_drafts,
            name="submission-draft-expiry",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._cleanup_error_reports,
            name="error-report-retention",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._keep_database_active,
            name="database-keepalive",
            interval=self._config.keepalive_interval_seconds,
            run_immediately=False,
        )

    @property
    def supervisor(self) -> BackgroundTaskSupervisor:
        """The task group owner, so `main` can attach process-level jobs to it."""
        return self._supervisor

    async def close(self) -> None:
        """Stop and await every worker job before application resources close."""
        await self._supervisor.close()

    def is_ready(self) -> bool:
        """Return whether every critical job has completed at least once."""
        required = {
            "core-domain-events",
            "schematic-jobs",
            "schematic-renders",
            "search-projections",
            "search-embeddings",
            "record-maintenance",
            "due-votes",
            "stale-build-locks",
            "schematic-job-cleanup",
            "media-storage-cleanup",
            "queue-health",
            "notification-retention",
            "idempotency-retention",
            "submission-draft-expiry",
            "submission-finalization",
        }
        if self._services.media_runner is not None:
            required.add("media-normalization")
        if self._schematic_pool is not None:
            required.add("schematic-pool-health")
        longest_interval = max(
            self._config.event_interval_seconds,
            self._config.maintenance_interval_seconds,
            self._config.schematic_job_interval_seconds,
            self._config.media_job_interval_seconds if self._services.media_runner is not None else 0,
            self._config.media_cleanup_interval_seconds,
            self._config.submission_finalization_interval_seconds,
            300,
        )
        return self._supervisor.is_healthy(required, max_age_seconds=longest_interval * 3)

    async def _process_events(self) -> None:
        async with self._event_lock:
            with trace_span("squid.worker.domain_events", {"squid.surface": "background_loop"}):
                await self._events.process_batch()

    async def _process_schematic_jobs(self) -> None:
        with trace_span("squid.worker.schematic_jobs", {"squid.surface": "background_loop"}):
            await self._schematic_jobs.process_batch()

    async def _process_schematic_renders(self) -> None:
        with trace_span("squid.worker.schematic_renders", {"squid.surface": "background_loop"}):
            await self._schematic_renders.process_batch()

    async def _process_media_jobs(self) -> None:
        runner = self._services.media_runner
        if runner is None:
            return
        with trace_span("squid.worker.media_normalization", {"squid.surface": "background_loop"}):
            await runner.process_batch(limit=self._config.media_job_concurrency)

    async def _cleanup_media_storage(self) -> None:
        with trace_span("squid.worker.media_storage_cleanup", {"squid.surface": "background_loop"}):
            await self._services.media_cleanup.process_batch()

    async def _process_submission_finalization(self) -> None:
        with trace_span("squid.worker.submission_finalization", {"squid.surface": "background_loop"}):
            await self._services.submission_finalization.process_batch()

    async def _refresh_search(self) -> None:
        with trace_span("squid.worker.search_projection", {"squid.surface": "background_loop"}):
            succeeded, failed = await self._services.refresh_search_index()
        if failed:
            logger.warning(
                "Search projection batch completed with failures",
                extra={"squid.queue.succeeded": succeeded, "squid.queue.failed": failed},
            )

    async def _process_search_embeddings(self) -> None:
        with trace_span("squid.worker.search_embeddings", {"squid.surface": "background_loop"}):
            succeeded, failed = await self._services.search_embeddings.process_batch()
        if failed:
            logger.warning(
                "Search embedding batch completed with failures",
                extra={"squid.queue.succeeded": succeeded, "squid.queue.failed": failed},
            )

    async def _process_records(self) -> None:
        with trace_span("squid.worker.record_maintenance", {"squid.surface": "background_loop"}):
            await self._services.records.process_queue()

    async def _close_due_votes(self) -> None:
        with trace_span("squid.worker.close_due_votes", {"squid.surface": "background_loop"}):
            now = Instant.now()
            snapshots = await self._services.votes.close_due(now)
        for snapshot in snapshots:
            if snapshot.poll is not None:
                record_histogram(
                    "squid.vote.close.lag",
                    max((now - snapshot.poll.deadline).total("seconds"), 0.0),
                    attributes={"squid.vote.kind": snapshot.kind},
                )

    async def _clean_stale_build_locks(self) -> None:
        with trace_span("squid.worker.stale_build_locks", {"squid.surface": "background_loop"}):
            await self._services.builds.clean_stale_locks(older_than=Instant.now().subtract(minutes=5))

    async def _cleanup_schematic_jobs(self) -> None:
        with trace_span("squid.worker.schematic_job_cleanup", {"squid.surface": "background_loop"}):
            await self._schematic_jobs.cleanup()

    async def _cleanup_notifications(self) -> None:
        with trace_span("squid.worker.notification_retention", {"squid.surface": "background_loop"}):
            await self._services.notifications.cleanup()

    async def _cleanup_idempotency(self) -> None:
        with trace_span("squid.worker.idempotency_retention", {"squid.surface": "background_loop"}):
            deleted = await self._services.purge_idempotency()
        if deleted:
            logger.info(
                "Expired idempotency requests removed",
                extra={"squid.idempotency.deleted": deleted},
            )

    async def _expire_submission_drafts(self) -> None:
        with trace_span("squid.worker.submission_draft_expiry", {"squid.surface": "background_loop"}):
            expired = await self._services.expire_submission_drafts()
        if expired:
            logger.info(
                "Expired inactive submission drafts",
                extra={"squid.submissions.drafts_expired": expired},
            )

    async def _cleanup_error_reports(self) -> None:
        with trace_span("squid.worker.error_report_retention", {"squid.surface": "background_loop"}):
            deleted = await self._services.error_reports.purge_expired()
        if deleted:
            logger.info(
                "Expired error reports removed",
                extra={"squid.diagnostics.deleted": deleted},
            )


async def main(process_config: WorkerProcessConfig | None = None, *, stop_event: asyncio.Event | None = None) -> None:
    """Run the worker until a process signal or caller-owned stop event fires."""
    resolved_config = process_config or load_or_exit(load_worker_process_config)
    configure_service_worker_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
    observability = configure_observability(resolved_config.observability, service_name="worker")
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop_event is None:
        for process_signal in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(process_signal, stop.set)

    try:
        async with create_worker_runtime(resolved_config.runtime) as runtime, AsyncExitStack() as analyzers:
            schematic_config = resolved_config.runtime.schematics
            if schematic_config.enabled and engine_installed():
                # `running()` owns the workers' stderr pumps, and an anyio task group can only
                # be exited by the task that entered it -- this one.
                pool = SchematicWorkerPool(schematic_config)
                native_analyzer = await analyzers.enter_async_context(pool.running())
            else:
                native_analyzer = NullSchematicAnalyzer("The worker does not have the schematic engine installed.")
            schematic_jobs = SchematicJobRunner(
                runtime.services.schematic_jobs,
                runtime.services.artifacts,
                native_analyzer,
                schematic_config,
            )
            # The worker subscribes to nothing, so its bus stays empty and the bridge is
            # purely an outbound path: a finished render tells the bot's panels to repaint.
            topic_bridge = await open_topic_bridge(resolved_config.runtime.database, LocalTopicBus())
            schematic_renders = SchematicRenderProjector(
                runtime.services.schematic_renders,
                runtime.services.schematics,
                runtime.services.artifacts,
                str(schematic_config.render_public_base_url)
                if schematic_config.render_public_base_url is not None
                else None,
                enabled=schematic_config.render_enabled,
                topics=topic_bridge,
            )
            worker = DatabaseWorker(
                runtime.services,
                runtime.keep_database_active,
                resolved_config.worker,
                schematic_jobs,
                schematic_renders,
                schematic_pool=native_analyzer if isinstance(native_analyzer, SchematicWorkerPool) else None,
            )

            async def worker_ready() -> bool:
                await runtime.ready()
                return worker.is_ready()

            # The supervisor's task group must be entered and exited by the same
            # task, so it is held here rather than inside DatabaseWorker.
            async with worker.running():
                worker.start()
                if topic_bridge is not None:
                    worker.supervisor.start(topic_bridge.run(), name="layout-topic-bridge")
                start_log_capture(
                    worker.supervisor,
                    runtime.services.error_reports,
                    enabled=resolved_config.diagnostics.capture_logged_errors,
                    capacity=resolved_config.diagnostics.log_capture_queue,
                )
                try:
                    async with ProcessHealthServer(worker_ready, port=resolved_config.worker.health_port):
                        await stop.wait()
                finally:
                    await worker.close()
                    if topic_bridge is not None:
                        with anyio.move_on_after(3.0):
                            await topic_bridge.pool.close()
                        topic_bridge.pool.terminate()
                    # The pool's own `running()` closes it; only the null analyzer, which owns
                    # no task group and so is not on the stack, still needs closing here.
                    if not isinstance(native_analyzer, SchematicWorkerPool):
                        await native_analyzer.aclose()
    finally:
        observability.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
