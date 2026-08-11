"""Application runtime lifecycle tests."""

import asyncio
from dataclasses import fields
from unittest.mock import AsyncMock

from squid.runtime import ApiServices, ApplicationRuntime, BackgroundTaskSupervisor, BotServices, WorkerServices


def test_process_service_bundles_expose_only_owned_capabilities() -> None:
    api = {field.name for field in fields(ApiServices)}
    bot = {field.name for field in fields(BotServices)}
    worker = {field.name for field in fields(WorkerServices)}

    assert api.isdisjoint({"build_inference", "discord_sync", "domain_events", "search_embeddings"})
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
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation() -> None:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    supervisor = BackgroundTaskSupervisor()
    supervisor.start_periodic(operation, name="test-job", interval=60)
    await entered.wait()

    assert supervisor.is_healthy({"test-job"}, max_age_seconds=1) is False
    await supervisor.close()

    assert cancelled.is_set()


async def test_background_task_supervisor_reports_fresh_periodic_heartbeats() -> None:
    completed = asyncio.Event()

    async def operation() -> None:
        completed.set()

    supervisor = BackgroundTaskSupervisor()
    supervisor.start_periodic(operation, name="heartbeat", interval=60)
    await completed.wait()
    await asyncio.sleep(0)

    assert supervisor.is_healthy({"heartbeat"}, max_age_seconds=1) is True
    assert supervisor.is_healthy({"heartbeat", "missing"}, max_age_seconds=1) is False
    await supervisor.close()


async def test_background_task_supervisor_bounds_feature_cancellation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_operation() -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()

    supervisor = BackgroundTaskSupervisor(shutdown_timeout=0.01)
    task = supervisor.start(stubborn_operation(), name="stubborn")
    await entered.wait()

    await supervisor.cancel(task)

    assert task.done() is False
    release.set()
    await task
    await supervisor.close()
