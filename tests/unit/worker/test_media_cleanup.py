"""Worker scheduling coverage for always-on media storage cleanup."""

from squid.config import WorkerConfig
from tests.unit.worker.fakes import MediaCleanupRecorder, SupervisorRecorder, make_worker, worker_services


async def test_worker_schedules_media_cleanup_when_normalization_is_disabled() -> None:
    cleanup = MediaCleanupRecorder()
    supervisor = SupervisorRecorder()
    worker = make_worker(
        worker_services(cleanup=cleanup),
        config=WorkerConfig(media_job_interval_seconds=0.25, media_cleanup_interval_seconds=7),
        supervisor=supervisor,
    )

    worker.start()

    cleanup_job = supervisor.job("media-storage-cleanup")
    assert cleanup_job.interval == 7
    await cleanup_job.operation()
    assert cleanup.processed == 1
    assert all(job.name != "media-normalization" for job in supervisor.jobs)
