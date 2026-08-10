"""Worker scheduling tests for bounded idempotency replay retention."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker


async def test_worker_schedules_and_invokes_explicit_idempotency_purge() -> None:
    services = SimpleNamespace(
        votes=Mock(),
        builds=Mock(),
        notifications=Mock(),
        events=Mock(),
        event_wake_listener=None,
        media_runner=None,
        record_queue_health=AsyncMock(),
        purge_idempotency=AsyncMock(return_value=3),
    )
    supervisor = Mock()
    worker = DatabaseWorker(
        cast(Any, services),
        AsyncMock(),
        WorkerConfig(maintenance_interval_seconds=30),
        Mock(),
        Mock(),
        supervisor=cast(Any, supervisor),
    )

    worker.start()

    retention_call = next(
        call for call in supervisor.start_periodic.call_args_list if call.kwargs["name"] == "idempotency-retention"
    )
    assert retention_call.kwargs["interval"] == 300
    await retention_call.args[0]()
    services.purge_idempotency.assert_awaited_once_with()
