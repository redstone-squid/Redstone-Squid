"""Worker scheduling coverage for always-on media storage cleanup."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from squid.config import WorkerConfig
from squid.worker.app import DatabaseWorker


async def test_worker_schedules_media_cleanup_when_normalization_is_disabled() -> None:
    cleanup = AsyncMock()
    services = SimpleNamespace(
        votes=Mock(),
        builds=Mock(),
        notifications=Mock(),
        events=Mock(),
        event_wake_listener=None,
        media_runner=None,
        media_cleanup=cleanup,
        record_queue_health=AsyncMock(),
        purge_idempotency=AsyncMock(return_value=0),
        expire_submission_drafts=AsyncMock(return_value=0),
        error_reports=Mock(),
    )
    supervisor = Mock()
    worker = DatabaseWorker(
        cast(Any, services),
        AsyncMock(),
        WorkerConfig(media_job_interval_seconds=0.25, media_cleanup_interval_seconds=7),
        Mock(),
        Mock(),
        supervisor=cast(Any, supervisor),
    )

    worker.start()

    cleanup_call = next(
        call for call in supervisor.start_periodic.call_args_list if call.kwargs["name"] == "media-storage-cleanup"
    )
    assert cleanup_call.kwargs["interval"] == 7
    await cleanup_call.args[0]()
    cleanup.process_batch.assert_awaited_once_with()
    assert all(call.kwargs["name"] != "media-normalization" for call in supervisor.start_periodic.call_args_list)
