"""Worker scheduling coverage for always-on media storage cleanup."""

from typing import Any, cast

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker
from tests.unit.worker.fakes import MediaCleanupRecorder, SupervisorRecorder, worker_services


async def test_worker_schedules_media_cleanup_when_normalization_is_disabled() -> None:
    cleanup = MediaCleanupRecorder()
    supervisor = SupervisorRecorder()
    worker = DatabaseWorker(
        worker_services(cleanup=cleanup),
        cast(Any, object()),
        WorkerConfig(media_job_interval_seconds=0.25, media_cleanup_interval_seconds=7),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
    )

    worker.start()

    cleanup_job = supervisor.job("media-storage-cleanup")
    assert cleanup_job.interval == 7
    await cleanup_job.operation()
    assert cleanup.processed == 1
    assert all(job.name != "media-normalization" for job in supervisor.jobs)
