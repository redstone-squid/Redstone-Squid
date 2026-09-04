"""Stateful worker fakes shared by scheduling tests."""

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, cast

from squid.builds.application import BuildService
from squid.diagnostics.application import ErrorReportService
from squid.events import DomainEventService
from squid.media.application.jobs import MediaStorageCleanup
from squid.notifications import NotificationService
from squid.runtime import BackgroundTaskSupervisor, JobHandle, WorkerServices
from squid.voting.application import VoteService


class StubBuildService(BuildService):
    """Supply the build-service identity required by worker event handlers."""

    def __init__(self) -> None:
        pass


class StubVoteService(VoteService):
    """Supply the vote-service identity required by worker event handlers."""

    def __init__(self) -> None:
        pass


class StubEventService(DomainEventService):
    """Supply the event-service identity required by the core event runner."""

    def __init__(self) -> None:
        pass


class StubNotificationService(NotificationService):
    """Supply the notification-service identity required by worker handlers."""

    def __init__(self) -> None:
        pass


class StubErrorReportService(ErrorReportService):
    """Supply the error-report service captured by the supervisor."""

    def __init__(self) -> None:
        pass


class MediaCleanupRecorder(MediaStorageCleanup):
    """Record storage cleanup batches without constructing persistence."""

    def __init__(self) -> None:
        self.processed = 0

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

    def capture_failures_into(self, service: ErrorReportService | None) -> None:
        self.captured_errors = service

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
    """Build the concrete worker service container around stateful test services."""
    maintenance = maintenance or MaintenanceRecorder()
    return WorkerServices(
        builds=StubBuildService(),
        error_reports=StubErrorReportService(),
        artifacts=cast(Any, object()),
        votes=StubVoteService(),
        records=cast(Any, object()),
        events=events or StubEventService(),
        event_wake_listener=None,
        notifications=notifications or StubNotificationService(),
        schematics=cast(Any, object()),
        schematic_jobs=cast(Any, object()),
        schematic_renders=cast(Any, object()),
        media_runner=None,
        media_cleanup=cleanup or MediaCleanupRecorder(),
        submission_finalization=cast(Any, object()),
        search_embeddings=cast(Any, object()),
        refresh_search_index=cast(Any, object()),
        record_queue_health=maintenance.record_queue_health,
        purge_idempotency=maintenance.purge_idempotency,
        expire_submission_drafts=maintenance.expire_submission_drafts,
    )
