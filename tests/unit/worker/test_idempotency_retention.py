"""Worker scheduling tests for bounded idempotency replay retention."""

from typing import Any, cast

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker
from tests.unit.worker.fakes import MaintenanceRecorder, SupervisorRecorder, worker_services


async def test_worker_schedules_and_invokes_explicit_idempotency_purge() -> None:
    maintenance = MaintenanceRecorder(purge_result=3)
    supervisor = SupervisorRecorder()
    worker = DatabaseWorker(
        worker_services(maintenance=maintenance),
        cast(Any, object()),
        WorkerConfig(maintenance_interval_seconds=30),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
    )

    worker.start()

    retention_job = supervisor.job("idempotency-retention")
    assert retention_job.interval == 300
    await retention_job.operation()
    assert maintenance.purge_calls == 1
