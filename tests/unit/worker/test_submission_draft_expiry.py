"""Worker scheduling coverage for synchronized-draft retention."""

from typing import Any, cast

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker
from tests.unit.worker.fakes import MaintenanceRecorder, SupervisorRecorder, worker_services


async def test_worker_schedules_and_invokes_bounded_draft_expiry() -> None:
    maintenance = MaintenanceRecorder(expiry_result=2)
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

    expiry_job = supervisor.job("submission-draft-expiry")
    assert expiry_job.interval == 300
    await expiry_job.operation()
    assert maintenance.expiry_calls == 1
