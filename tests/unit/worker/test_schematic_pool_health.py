# pyright: reportPrivateUsage=false
"""Saturation reporting for the schematic worker pool."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from pytest_mock import MockerFixture

from squid.config import SchematicConfig, WorkerConfig
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.worker.app import DatabaseWorker


def _services() -> SimpleNamespace:
    return SimpleNamespace(
        votes=Mock(),
        builds=Mock(),
        notifications=Mock(),
        events=Mock(),
        event_wake_listener=None,
        media_runner=None,
        media_cleanup=AsyncMock(),
        record_queue_health=AsyncMock(),
        purge_idempotency=AsyncMock(return_value=0),
        expire_submission_drafts=AsyncMock(return_value=0),
        error_reports=Mock(),
    )


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
    supervisor = Mock()
    pool = SchematicWorkerPool(SchematicConfig(workers=1))
    worker = DatabaseWorker(
        cast(Any, _services()),
        AsyncMock(),
        WorkerConfig(maintenance_interval_seconds=11),
        Mock(),
        Mock(),
        supervisor=cast(Any, supervisor),
        schematic_pool=pool,
    )

    worker.start()

    health_call = next(
        call for call in supervisor.start_periodic.call_args_list if call.kwargs["name"] == "schematic-pool-health"
    )
    assert health_call.kwargs["interval"] == 11
    assert health_call.args[0] == pool.record_health


async def test_worker_skips_pool_health_without_the_native_pool() -> None:
    supervisor = Mock()
    worker = DatabaseWorker(
        cast(Any, _services()),
        AsyncMock(),
        WorkerConfig(),
        Mock(),
        Mock(),
        supervisor=cast(Any, supervisor),
    )

    worker.start()
    worker.is_ready()

    assert all(call.kwargs["name"] != "schematic-pool-health" for call in supervisor.start_periodic.call_args_list)
    # Readiness must not wait on a job that was never scheduled.
    assert "schematic-pool-health" not in supervisor.is_healthy.call_args.args[0]
