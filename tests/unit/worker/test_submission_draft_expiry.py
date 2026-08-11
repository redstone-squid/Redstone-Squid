"""Worker scheduling coverage for synchronized-draft retention."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker


async def test_worker_schedules_and_invokes_bounded_draft_expiry() -> None:
    services = SimpleNamespace(
        votes=Mock(),
        builds=Mock(),
        notifications=Mock(),
        events=Mock(),
        event_wake_listener=None,
        media_runner=None,
        record_queue_health=AsyncMock(),
        purge_idempotency=AsyncMock(return_value=0),
        expire_submission_drafts=AsyncMock(return_value=2),
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

    expiry_call = next(
        call for call in supervisor.start_periodic.call_args_list if call.kwargs["name"] == "submission-draft-expiry"
    )
    assert expiry_call.kwargs["interval"] == 300
    await expiry_call.args[0]()
    services.expire_submission_drafts.assert_awaited_once_with()
