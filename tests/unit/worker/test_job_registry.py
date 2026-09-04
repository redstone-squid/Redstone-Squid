"""Worker scheduling and readiness parity from one immutable registry."""

from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker, WorkerJobSpec
from tests.unit.worker.fakes import SupervisorRecorder, worker_services


@pytest.mark.parametrize("media_enabled", [False, True])
def test_worker_scheduling_and_readiness_derive_from_the_same_registry(media_enabled: bool) -> None:
    services = worker_services()
    if media_enabled:
        services = replace(services, media_runner=cast(Any, object()))
    supervisor = SupervisorRecorder()
    worker = DatabaseWorker(
        services,
        cast(Any, object()),
        WorkerConfig(media_job_interval_seconds=5, maintenance_interval_seconds=11),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
    )

    worker.start()
    worker.is_ready()

    registered_names = tuple(spec.name for spec in worker.job_specs)
    assert tuple(job.name for job in supervisor.jobs) == registered_names
    assert ("media-normalization" in registered_names) is media_enabled
    assert supervisor.readiness_queries[-1] == frozenset(
        spec.name for spec in worker.job_specs if spec.critical
    )
    assert supervisor.readiness_max_ages[-1] == max(
        spec.interval_seconds for spec in worker.job_specs if spec.critical
    ) * 3


def test_worker_job_specs_are_frozen_values() -> None:
    async def run() -> None:
        pass

    spec = WorkerJobSpec("example", 1, critical=True, run=run)

    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"  # type: ignore[misc]
