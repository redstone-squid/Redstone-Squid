import asyncio

import anyio

from squid_reactivity.async_values import _Execution


async def test_observers_wait_without_starting_or_cancelling_owned_execution() -> None:
    execution = _Execution[int]()
    started = asyncio.Event()
    release = asyncio.Event()
    values: list[int] = []
    status = 0

    async def load() -> int:
        nonlocal status
        started.set()
        await release.wait()
        status = 42
        return status

    async def run() -> None:
        values.append(await execution.run(load, lambda: status))

    async def observe() -> None:
        values.append(await execution.wait())

    async def cancelled_observer(*, task_status: anyio.abc.TaskStatus[anyio.CancelScope]) -> None:
        with anyio.CancelScope() as scope:
            task_status.started(scope)
            await execution.wait()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(observe)
            scope = await tasks.start(cancelled_observer)
            await anyio.wait_all_tasks_blocked()
            assert not started.is_set()
            assert not execution.done
            tasks.start_soon(run)
            await started.wait()
            scope.cancel()
            await anyio.wait_all_tasks_blocked()
            assert not execution.done
            release.set()

    assert values == [42, 42]
    assert execution.done
    assert await execution.wait() == 42
    assert await execution.wait() == 42
