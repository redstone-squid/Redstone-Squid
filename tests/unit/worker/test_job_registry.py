"""Worker scheduling and readiness parity from one immutable registry."""

from dataclasses import FrozenInstanceError, replace
from typing import override

import pytest

from squid.config import WorkerConfig
from squid.media.application.jobs import MediaNormalizationJobRunner
from squid.worker.app import WorkerJobSpec
from squid.worker.rendering import SchematicPreviewWorker
from tests.unit.worker.fakes import SupervisorRecorder, make_worker, worker_services


class MediaRunnerRecorder(MediaNormalizationJobRunner):
    """Record normalization batches captured during registry construction."""

    def __init__(self) -> None:
        self.limits: list[int] = []

    @override
    async def process_batch(self, *, limit: int = 8) -> None:
        self.limits.append(limit)


class PreviewCleanupRecorder(SchematicPreviewWorker):
    def __init__(self) -> None:
        self.calls = 0

    @override
    async def cleanup(self, *, retention_hours: int = 24, limit: int = 50) -> int:
        self.calls += 1
        return 1


@pytest.mark.parametrize("media_enabled", [False, True])
def test_worker_scheduling_and_readiness_derive_from_the_same_registry(media_enabled: bool) -> None:
    services = worker_services()
    if media_enabled:
        services = replace(services, media_runner=MediaRunnerRecorder())
    supervisor = SupervisorRecorder()
    worker = make_worker(
        services,
        config=WorkerConfig(media_job_interval_seconds=5, maintenance_interval_seconds=11),
        supervisor=supervisor,
    )

    worker.start()
    worker.is_ready()

    registered_names = tuple(spec.name for spec in worker.job_specs)
    assert tuple(job.name for job in supervisor.jobs) == registered_names
    assert ("media-normalization" in registered_names) is media_enabled
    assert supervisor.readiness_queries[-1] == frozenset(spec.name for spec in worker.job_specs if spec.critical)
    assert (
        supervisor.readiness_max_ages[-1]
        == max(spec.interval_seconds for spec in worker.job_specs if spec.critical) * 3
    )


def test_worker_job_specs_are_frozen_values() -> None:
    async def run() -> None:
        pass

    spec = WorkerJobSpec("example", 1, critical=True, run=run)

    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"  # type: ignore[misc]


async def test_media_job_captures_the_runner_registered_at_construction() -> None:
    runner = MediaRunnerRecorder()
    services = replace(worker_services(), media_runner=runner)
    worker = make_worker(
        services,
        config=WorkerConfig(media_job_concurrency=3),
    )
    media_job = next(spec for spec in worker.job_specs if spec.name == "media-normalization")

    await media_job.run()

    assert runner.limits == [3]


async def test_preview_cleanup_is_registered_with_worker_maintenance() -> None:
    previews = PreviewCleanupRecorder()
    worker = make_worker(
        worker_services(),
        config=WorkerConfig(maintenance_interval_seconds=11),
        schematic_renders=previews,
    )
    cleanup = next(spec for spec in worker.job_specs if spec.name == "schematic-preview-cleanup")

    await cleanup.run()

    assert cleanup.interval_seconds == 300
    assert previews.calls == 1
