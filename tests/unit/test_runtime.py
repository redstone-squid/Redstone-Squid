"""Application runtime lifecycle tests."""

import contextlib
from typing import Any, cast

import anyio

from squid.runtime import ApplicationRuntime, BackgroundTaskSupervisor
from squid_ui_discord.testing import AsyncCallRecorder
from tests.support.services import bot_services


async def test_application_runtime_closes_database() -> None:
    close = AsyncCallRecorder()
    runtime = ApplicationRuntime(bot_services(), close, AsyncCallRecorder())

    async with runtime:
        pass

    close.assert_awaited_once_with()


async def test_application_runtime_checks_readiness() -> None:
    readiness = AsyncCallRecorder()
    runtime = ApplicationRuntime(bot_services(), AsyncCallRecorder(), AsyncCallRecorder(), readiness)

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

        assert supervisor.is_healthy({"heartbeat"}, max_age_seconds=1) is True
        assert supervisor.is_healthy({"heartbeat", "missing"}, max_age_seconds=1) is False


async def test_background_task_supervisor_captures_a_failed_run() -> None:
    """Keep a diagnostic reference for failures that have no request or response."""
    captured: list[dict[str, object]] = []
    report_recorded = anyio.Event()

    class Reports:
        async def record(self, error: BaseException, **kwargs: object) -> None:
            captured.append({"error": error, **kwargs})
            report_recorded.set()

    async def operation() -> None:
        msg = "job exploded"
        raise RuntimeError(msg)

    async with BackgroundTaskSupervisor().running() as supervisor:
        supervisor.capture_failures_into(cast(Any, Reports()))
        supervisor.start_periodic(operation, name="doomed", interval=60)
        with anyio.fail_after(5):
            await report_recorded.wait()
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
        # Shield the wait so repeated cancellation cannot finish it before the assertion.
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
    """Cancellation reaches the next checkpoint even after the job catches it once."""
    entered = anyio.Event()
    finished = anyio.Event()

    async def stubborn() -> None:
        entered.set()
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            with contextlib.suppress(anyio.get_cancelled_exc_class()):
                await anyio.sleep_forever()
            finished.set()
            raise

    with anyio.fail_after(5):
        async with BackgroundTaskSupervisor(shutdown_timeout=0.01).running() as supervisor:
            supervisor.start(stubborn(), name="stubborn")
            await entered.wait()

    assert finished.is_set()


async def test_background_task_supervisor_isolates_one_job_failure() -> None:
    """A task group cancels siblings on an unhandled error; the supervisor must not."""
    survivor_started = anyio.Event()
    survivor_cancelled = False

    async def failing() -> None:
        msg = "job blew up"
        raise RuntimeError(msg)

    async def survivor() -> None:
        nonlocal survivor_cancelled
        survivor_started.set()
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            survivor_cancelled = True
            raise

    async with BackgroundTaskSupervisor().running() as supervisor:
        survivor_handle = supervisor.start(survivor(), name="survivor")
        with anyio.fail_after(5):
            await survivor_started.wait()
            failing_handle = supervisor.start(failing(), name="failing")
            await failing_handle.finished.wait()

        assert survivor_cancelled is False
        assert survivor_handle.finished.is_set() is False
