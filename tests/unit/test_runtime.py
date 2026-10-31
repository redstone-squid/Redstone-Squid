"""Application runtime lifecycle tests."""

import asyncio
import contextlib
from dataclasses import fields
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio

from squid.runtime import ApiServices, ApplicationRuntime, BackgroundTaskSupervisor, BotServices, WorkerServices


def test_process_service_bundles_expose_only_owned_capabilities() -> None:
    api = {field.name for field in fields(ApiServices)}
    bot = {field.name for field in fields(BotServices)}
    worker = {field.name for field in fields(WorkerServices)}

    assert api.isdisjoint({"build_inference", "discord_reconciliation", "domain_events", "search_embeddings"})
    assert bot.isdisjoint(
        {"api_keys", "web_auth", "cli_authorization", "vote_members", "search_embeddings", "schematic_jobs"}
    )
    assert worker == {
        "builds",
        "artifacts",
        "votes",
        "records",
        "events",
        "event_wake_listener",
        "notifications",
        "schematics",
        "schematic_jobs",
        "schematic_renders",
        "media_runner",
        "media_cleanup",
        "submission_finalization",
        "search_embeddings",
        "refresh_search_index",
        "record_queue_health",
        "purge_idempotency",
        "expire_submission_drafts",
        # Shared with the API and bot bundles rather than owned: all three capture failures, and
        # the worker is only the one that sweeps expired reports.
        "error_reports",
    }


async def test_application_runtime_closes_database() -> None:
    database = AsyncMock()
    runtime = ApplicationRuntime(AsyncMock(), database.close, AsyncMock())

    async with runtime:
        pass

    database.close.assert_awaited_once_with()


async def test_application_runtime_checks_readiness() -> None:
    readiness = AsyncMock()
    runtime = ApplicationRuntime(AsyncMock(), AsyncMock(), AsyncMock(), readiness)

    await runtime.ready()

    readiness.assert_awaited_once_with()


async def test_background_task_supervisor_cancels_and_awaits_periodic_work() -> None:
    entered = anyio.Event()
    cancelled = anyio.Event()

    async def operation() -> None:
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    async with BackgroundTaskSupervisor().running() as supervisor:
        supervisor.start_periodic(operation, name="test-job", interval=60)
        await entered.wait()

        assert supervisor.is_healthy({"test-job"}, max_age_seconds=1) is False
        await supervisor.close()

        assert cancelled.is_set()


async def test_background_task_supervisor_reports_fresh_periodic_heartbeats() -> None:
    completed = anyio.Event()

    async def operation() -> None:
        completed.set()

    async with BackgroundTaskSupervisor().running() as supervisor:
        supervisor.start_periodic(operation, name="heartbeat", interval=60)
        await completed.wait()
        await asyncio.sleep(0)

        assert supervisor.is_healthy({"heartbeat"}, max_age_seconds=1) is True
        assert supervisor.is_healthy({"heartbeat", "missing"}, max_age_seconds=1) is False


async def test_background_task_supervisor_captures_a_failed_run() -> None:
    """A background job answers nobody, so a log line was the only trace it ever left."""
    captured: list[dict[str, object]] = []
    failed = anyio.Event()

    class Reports:
        async def record(self, error: BaseException, **kwargs: object) -> None:
            captured.append({"error": error, **kwargs})

    async def operation() -> None:
        failed.set()
        msg = "job exploded"
        raise RuntimeError(msg)

    async with BackgroundTaskSupervisor().running() as supervisor:
        supervisor.capture_failures_into(cast(Any, Reports()))
        supervisor.start_periodic(operation, name="doomed", interval=60)
        await failed.wait()
        await asyncio.sleep(0)
        await supervisor.close()

    assert captured
    assert captured[0]["surface"] == "background_job"
    assert captured[0]["origin"] == "doomed"
    correlation = captured[0]["correlation_id"]
    assert isinstance(correlation, str)
    assert captured[0]["reference"] == correlation[:12]


async def test_background_task_supervisor_survives_a_failing_report_store() -> None:
    """Losing the diagnostic must not also stop the loop that produced it."""
    runs = 0
    twice = anyio.Event()

    class Reports:
        async def record(self, error: BaseException, **kwargs: object) -> None:
            msg = "the database is down"
            raise RuntimeError(msg)

    async def operation() -> None:
        nonlocal runs
        runs += 1
        if runs >= 2:
            twice.set()
        msg = "job exploded"
        raise RuntimeError(msg)

    async with BackgroundTaskSupervisor().running() as supervisor:
        supervisor.capture_failures_into(cast(Any, Reports()))
        supervisor.start_periodic(operation, name="doomed", interval=0.001)
        await twice.wait()
        await supervisor.close()

    assert runs >= 2


async def test_background_task_supervisor_bounds_feature_cancellation() -> None:
    entered = anyio.Event()
    release = anyio.Event()

    async def stubborn_operation() -> None:
        entered.set()
        # Only a shielded scope genuinely resists cancellation now. Merely
        # catching the cancellation exception no longer stalls shutdown, because
        # the scope re-delivers it at the next checkpoint.
        with anyio.CancelScope(shield=True):
            await release.wait()

    async with BackgroundTaskSupervisor(shutdown_timeout=0.01).running() as supervisor:
        handle = supervisor.start(stubborn_operation(), name="stubborn")
        await entered.wait()

        await supervisor.cancel(handle)

        assert handle.finished.is_set() is False
        release.set()
        await handle.finished.wait()


async def test_background_task_supervisor_forces_down_a_job_that_swallows_cancellation() -> None:
    """A job catching the cancellation must not be able to outlast the deadline.

    A shielded job still can -- a task group cannot abandon a child the way the
    asyncio.wait this replaces could -- but nothing in the codebase shields.
    """
    entered = anyio.Event()
    finished = anyio.Event()

    async def stubborn() -> None:
        entered.set()
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            with contextlib.suppress(anyio.get_cancelled_exc_class()):
                await anyio.sleep(30)
            finished.set()
            raise

    with anyio.fail_after(5):
        async with BackgroundTaskSupervisor(shutdown_timeout=0.01).running() as supervisor:
            supervisor.start(stubborn(), name="stubborn")
            await entered.wait()

    assert finished.is_set()


async def test_background_task_supervisor_isolates_one_job_failure() -> None:
    """A task group cancels siblings on an unhandled error; the supervisor must not."""
    failing_started = anyio.Event()
    survivor_cancelled = False

    async def failing() -> None:
        failing_started.set()
        msg = "job blew up"
        raise RuntimeError(msg)

    async def survivor() -> None:
        nonlocal survivor_cancelled
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            survivor_cancelled = True
            raise

    async with BackgroundTaskSupervisor().running() as supervisor:
        survivor_handle = supervisor.start(survivor(), name="survivor")
        supervisor.start(failing(), name="failing")
        await failing_started.wait()
        await asyncio.sleep(0)

        assert survivor_cancelled is False
        assert survivor_handle.finished.is_set() is False
