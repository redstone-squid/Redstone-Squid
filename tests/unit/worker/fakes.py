"""Stateful worker fakes shared by scheduling tests."""

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import cast, override

from squid.config import WorkerConfig
from squid.diagnostics.application import ErrorReportService
from squid.events import DomainEventService
from squid.media.application.jobs import MediaStorageCleanup
from squid.notifications import NotificationService
from squid.runtime import BackgroundTaskSupervisor, JobHandle, WorkerServices
from squid.schematics.infrastructure.durable import SchematicJobRunner
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.worker.app import DatabaseWorker
from squid.worker.rendering import SchematicPreviewWorker
from tests.support.services import unconfigured
from tests.support.services import worker_services as checked_worker_services


def make_worker(
    services: WorkerServices,
    *,
    config: WorkerConfig | None = None,
    supervisor: BackgroundTaskSupervisor | None = None,
    schematic_renders: SchematicPreviewWorker | None = None,
    schematic_pool: SchematicWorkerPool | None = None,
) -> DatabaseWorker:
    """Construct a worker with signature-checked defaults for unrelated infrastructure."""

    async def keep_database_active() -> None:
        pass

    return DatabaseWorker(
        services,
        keep_database_active,
        config or WorkerConfig(),
        unconfigured(SchematicJobRunner),
        schematic_renders if schematic_renders is not None else unconfigured(SchematicPreviewWorker),
        supervisor=supervisor,
        schematic_pool=schematic_pool,
    )


class MediaCleanupRecorder(MediaStorageCleanup):
    """Record storage cleanup batches without constructing persistence."""

    def __init__(self) -> None:
        self.processed = 0

    @override
    async def process_batch(self, *, limit: int = 100) -> None:
        self.processed += 1


@dataclass(slots=True)
class MaintenanceRecorder:
    """Record calls to callable maintenance capabilities."""

    purge_result: int = 0
    expiry_result: int = 0
    queue_health_calls: int = 0
    purge_calls: int = 0
    expiry_calls: int = 0

    async def record_queue_health(self) -> None:
        self.queue_health_calls += 1

    async def purge_idempotency(self) -> int:
        self.purge_calls += 1
        return self.purge_result

    async def cleanup_submission_schematics(self) -> None:
        pass

    async def cleanup_submission_inference(self) -> None:
        pass

    async def expire_submission_drafts(self) -> int:
        self.expiry_calls += 1
        return self.expiry_result


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """One periodic operation registered with the worker supervisor."""

    operation: Callable[[], Awaitable[None]]
    name: str
    interval: float
    run_immediately: bool


@dataclass(slots=True)
class SupervisorRecorder(BackgroundTaskSupervisor):
    """Record worker ownership and readiness requests without starting tasks."""

    jobs: list[ScheduledJob] = field(default_factory=list)
    readiness_queries: list[frozenset[str]] = field(default_factory=list)
    readiness_max_ages: list[float] = field(default_factory=list)
    captured_errors: ErrorReportService | None = None
    healthy: bool = True

    @override
    def capture_failures_into(self, service: ErrorReportService | None) -> None:
        self.captured_errors = service

    @override
    def start_periodic(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
        interval: float,
        run_immediately: bool = True,
    ) -> JobHandle:
        self.jobs.append(ScheduledJob(operation, name, interval, run_immediately))
        return cast(JobHandle, object())

    @override
    def is_healthy(self, required: Collection[str], *, max_age_seconds: float) -> bool:
        self.readiness_queries.append(frozenset(required))
        self.readiness_max_ages.append(max_age_seconds)
        return self.healthy

    def job(self, name: str) -> ScheduledJob:
        return next(job for job in self.jobs if job.name == name)


def worker_services(
    *,
    cleanup: MediaCleanupRecorder | None = None,
    maintenance: MaintenanceRecorder | None = None,
    events: DomainEventService | None = None,
    notifications: NotificationService | None = None,
) -> WorkerServices:
    """Build the worker service graph around the stateful services these scheduling tests observe."""
    maintenance = maintenance or MaintenanceRecorder()
    observed: dict[str, object] = {
        "media_cleanup": cleanup or MediaCleanupRecorder(),
        "record_queue_health": maintenance.record_queue_health,
        "purge_idempotency": maintenance.purge_idempotency,
        "cleanup_submission_schematics": maintenance.cleanup_submission_schematics,
        "cleanup_submission_inference": maintenance.cleanup_submission_inference,
        "expire_submission_drafts": maintenance.expire_submission_drafts,
    }
    if events is not None:
        observed["events"] = events
    if notifications is not None:
        observed["notifications"] = notifications
    return checked_worker_services(**observed)
