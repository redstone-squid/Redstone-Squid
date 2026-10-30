"""Execution admission distinguishes new operations from cleanup during shutdown."""

import asyncio

import anyio
import pytest

from squid_ui.runtime import UIExecutionHost


@pytest.mark.parametrize("kind", ["operation", "cleanup"])
async def test_drain_rejects_operations_but_accepts_nested_cleanup(kind: str) -> None:
    entered, closing, nested, release, closed = (asyncio.Event() for _ in range(5))
    events: list[str] = []
    host = UIExecutionHost()

    async def late_operation() -> None:
        pytest.fail("operation submitted during closure must not run")

    async def cleanup() -> None:
        nested.set()
        await release.wait()
        events.append("cleanup")

    async def work() -> None:
        entered.set()
        await closing.wait()
        with pytest.raises(RuntimeError, match="running UIExecutionHost"):
            host.start_operation(late_operation)
        host.start_cleanup(cleanup)
        events.append("work")

    async def close() -> None:
        closing.set()
        await host.close()
        closed.set()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            await tasks.start(host.run)
            submit = host.start_operation if kind == "operation" else host.start_cleanup
            submit(work)
            await entered.wait()
            tasks.start_soon(close)
            await nested.wait()
            assert not closed.is_set()
            with pytest.raises(RuntimeError, match="running UIExecutionHost"):
                host.start_cleanup(cleanup)
            release.set()
            await closed.wait()
        assert events == ["work", "cleanup"]
        for submit in (host.start_operation, host.start_cleanup):
            with pytest.raises(RuntimeError, match="running UIExecutionHost"):
                submit(late_operation)


async def test_host_cancellation_drains_operations_and_isolates_failure(caplog: pytest.LogCaptureFixture) -> None:
    entered, release, completed = (asyncio.Event() for _ in range(3))
    host = UIExecutionHost()

    async def operation() -> None:
        entered.set()
        await release.wait()
        completed.set()

    async def failing_operation() -> None:
        raise ValueError("operation failed")

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            await tasks.start(host.run)
            host.start_operation(operation)
            host.start_operation(failing_operation)
            await entered.wait()
            tasks.cancel_scope.cancel()
            release.set()
        assert completed.is_set()
        assert "host operation failed" in caplog.text
