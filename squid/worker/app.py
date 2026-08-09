"""Database maintenance worker and process entry point."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from whenever import Instant

from squid.artifacts.infrastructure import create_artifact_store
from squid.bootstrap import create_application_runtime
from squid.builds.application import BuildService
from squid.config import WorkerConfig, WorkerProcessConfig, load_worker_process_config
from squid.events import DomainEventService
from squid.health import ProcessHealthServer
from squid.logging_config import configure_service_worker_logging
from squid.observability import configure_observability, record_histogram, trace_span
from squid.records.application import RecordComputationService
from squid.runtime import ApplicationServices, BackgroundTaskSupervisor
from squid.schematics.application import SchematicJobService, SchematicService
from squid.schematics.infrastructure.capability import NullSchematicAnalyzer, engine_installed
from squid.schematics.infrastructure.durable import SchematicJobRunner
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.search.application import SearchEmbeddingService
from squid.voting.application import VoteService
from squid.worker.events import ApplyBuildVoteOutcomeHandler, CoreDomainEventRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerServices:
    """Narrow application capabilities used by this process."""

    builds: BuildService
    votes: VoteService
    records: RecordComputationService
    events: DomainEventService
    schematics: SchematicService
    schematic_jobs: SchematicJobService
    search_embeddings: SearchEmbeddingService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]

    @classmethod
    def from_application(cls, services: ApplicationServices) -> "WorkerServices":
        """Project the shared composition graph into worker-owned capabilities."""
        return cls(
            builds=services.builds,
            votes=services.votes,
            records=services.record_computation,
            events=services.domain_events,
            schematics=services.schematics,
            schematic_jobs=services.schematic_jobs,
            search_embeddings=services.search_embeddings,
            refresh_search_index=services.refresh_search_index,
        )


class DatabaseWorker:
    """Own periodic database-only application work."""

    def __init__(
        self,
        services: WorkerServices,
        keep_database_active: Callable[[], Awaitable[None]],
        config: WorkerConfig,
        schematic_jobs: SchematicJobRunner,
        *,
        supervisor: BackgroundTaskSupervisor | None = None,
    ) -> None:
        self._services = services
        self._keep_database_active = keep_database_active
        self._config = config
        self._schematic_jobs = schematic_jobs
        self._supervisor = supervisor or BackgroundTaskSupervisor()
        outcome_handler = ApplyBuildVoteOutcomeHandler(services.votes, services.builds)
        self._events = CoreDomainEventRunner(services.events, {"vote_session.closed": (outcome_handler,)})

    def start(self) -> None:
        """Start all jobs after the runtime has been constructed successfully."""
        event_interval = self._config.event_interval_seconds
        maintenance_interval = self._config.maintenance_interval_seconds
        self._supervisor.start_periodic(
            self._process_events,
            name="core-domain-events",
            interval=event_interval,
        )
        self._supervisor.start_periodic(
            self._process_schematic_jobs,
            name="schematic-jobs",
            interval=self._config.schematic_job_interval_seconds,
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
            self._maintain_artifacts,
            name="artifact-maintenance",
            interval=maintenance_interval,
        )
        self._supervisor.start_periodic(
            self._cleanup_schematic_jobs,
            name="schematic-job-cleanup",
            interval=max(maintenance_interval, 300),
        )
        self._supervisor.start_periodic(
            self._keep_database_active,
            name="database-keepalive",
            interval=self._config.keepalive_interval_seconds,
            run_immediately=False,
        )

    async def close(self) -> None:
        """Stop and await every worker job before application resources close."""
        await self._supervisor.close()

    def is_ready(self) -> bool:
        """Return whether every critical job has completed at least once."""
        required = {
            "core-domain-events",
            "schematic-jobs",
            "search-projections",
            "search-embeddings",
            "record-maintenance",
            "due-votes",
            "stale-build-locks",
            "artifact-maintenance",
            "schematic-job-cleanup",
        }
        longest_interval = max(
            self._config.event_interval_seconds,
            self._config.maintenance_interval_seconds,
            self._config.schematic_job_interval_seconds,
            300,
        )
        return self._supervisor.is_healthy(required, max_age_seconds=longest_interval * 3)

    async def _process_events(self) -> None:
        with trace_span("squid.worker.domain_events", {"squid.surface": "background_loop"}):
            await self._events.process_batch()

    async def _process_schematic_jobs(self) -> None:
        with trace_span("squid.worker.schematic_jobs", {"squid.surface": "background_loop"}):
            await self._schematic_jobs.process_batch()

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

    async def _maintain_artifacts(self) -> None:
        with trace_span("squid.worker.artifact_maintenance", {"squid.surface": "background_loop"}):
            migrated, recovered = await self._services.schematics.maintain_storage()
        if migrated or recovered:
            logger.info(
                "Schematic artifact maintenance completed",
                extra={"squid.artifacts.migrated": migrated, "squid.artifacts.recovered": recovered},
            )

    async def _cleanup_schematic_jobs(self) -> None:
        with trace_span("squid.worker.schematic_job_cleanup", {"squid.surface": "background_loop"}):
            await self._schematic_jobs.cleanup()


async def main(process_config: WorkerProcessConfig | None = None, *, stop_event: asyncio.Event | None = None) -> None:
    """Run the worker until a process signal or caller-owned stop event fires."""
    resolved_config = process_config or load_worker_process_config()
    configure_service_worker_logging(resolved_config.logging)
    observability = configure_observability(resolved_config.observability, service_name="worker")
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop_event is None:
        for process_signal in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(process_signal, stop.set)

    try:
        async with create_application_runtime(resolved_config.runtime) as runtime:
            artifacts = create_artifact_store(resolved_config.runtime.object_storage)
            schematic_config = resolved_config.runtime.schematics
            if schematic_config.enabled and engine_installed():
                native_analyzer = SchematicWorkerPool(schematic_config)
            else:
                native_analyzer = NullSchematicAnalyzer("The worker does not have the schematic engine installed.")
            schematic_jobs = SchematicJobRunner(
                runtime.services.schematic_jobs,
                artifacts,
                native_analyzer,
                schematic_config,
            )
            worker = DatabaseWorker(
                WorkerServices.from_application(runtime.services),
                runtime.keep_database_active,
                resolved_config.worker,
                schematic_jobs,
            )
            worker.start()

            async def worker_ready() -> bool:
                await runtime.ready()
                return worker.is_ready()

            try:
                async with ProcessHealthServer(worker_ready, port=resolved_config.worker.health_port):
                    await stop.wait()
            finally:
                await worker.close()
                await native_analyzer.aclose()
    finally:
        observability.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
