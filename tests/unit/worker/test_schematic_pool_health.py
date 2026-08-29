# pyright: reportPrivateUsage=false
"""Saturation reporting for the schematic worker pool."""

from typing import Any, cast

from pytest_mock import MockerFixture

from squid.config import SchematicConfig, WorkerConfig
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.worker.app import DatabaseWorker
from tests.unit.worker.fakes import SupervisorRecorder, worker_services


async def test_pool_reports_idle_in_flight_and_waiting_counts(mocker: MockerFixture) -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=3))
    pool._idle.pop()
    pool._waiting = 2
    gauge = mocker.patch.object(worker_module, "record_gauge")

    await pool.record_health()

    recorded = {call.args[0]: call.args[1] for call in gauge.call_args_list}
    assert recorded["squid.schematic.pool.idle_workers"] == 2
    assert recorded["squid.schematic.pool.in_flight"] == 1
    assert recorded["squid.schematic.pool.waiters"] == 2
    assert recorded["squid.schematic.pool.breaker_open"] == 0


async def test_worker_samples_pool_health_only_when_a_pool_exists() -> None:
    supervisor = SupervisorRecorder()
    pool = SchematicWorkerPool(SchematicConfig(workers=1))
    worker = DatabaseWorker(
        worker_services(),
        cast(Any, object()),
        WorkerConfig(maintenance_interval_seconds=11),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
        schematic_pool=pool,
    )

    worker.start()

    health_job = supervisor.job("schematic-pool-health")
    assert health_job.interval == 11
    assert health_job.operation == pool.record_health


async def test_worker_skips_pool_health_without_the_native_pool() -> None:
    supervisor = SupervisorRecorder()
    worker = DatabaseWorker(
        worker_services(),
        cast(Any, object()),
        WorkerConfig(),
        cast(Any, object()),
        cast(Any, object()),
        supervisor=supervisor,
    )

    worker.start()
    worker.is_ready()

    assert all(job.name != "schematic-pool-health" for job in supervisor.jobs)
    # Readiness must not wait on a job that was never scheduled.
    assert "schematic-pool-health" not in supervisor.readiness_queries[-1]
