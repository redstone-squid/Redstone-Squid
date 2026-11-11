import asyncio

import anyio
import pytest

from squid_reactivity import Reactive, state
from squid_reactivity.errors import ReactiveCycleError
from squid_reactivity.operations import Failed, OperationExecution, ProgressReporter, operation
from squid_reactivity.resources import Ready, resource


async def test_resource_awaits_operation_without_inheriting_its_inputs() -> None:
    class Model(Reactive):
        input: int = state(2)
        loads: int = 0

        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            return self.input * 2

        @resource
        async def result(self) -> int:
            self.loads += 1
            return await execution

    model = Model()
    execution = model.work.start()
    assert await model.result == 4
    model.input = 3
    assert model.result.status == Ready(4)
    assert await model.result == 4
    assert model.loads == 1


async def test_operation_awaits_resource_without_repeating_when_resource_changes() -> None:
    class Model(Reactive):
        input: int = state(2)

        @resource
        async def source(self) -> int:
            return self.input

        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            return await self.source * 2

    model = Model()
    execution = model.work.start()
    assert await execution == 4
    model.input = 3
    assert await model.source == 3
    assert await execution == 4
    assert await model.work.start() == 6


async def test_resources_share_an_operation_started_by_another_caller() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Model(Reactive):
        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return 7

        @resource
        async def left(self) -> int:
            return await execution

        @resource
        async def right(self) -> int:
            return await execution

    model = Model()
    execution = model.work.start()
    values: list[int] = []

    async def run_operation() -> None:
        values.append(await execution)

    async def run_resources() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(load_left)
            tasks.start_soon(load_right)

    async def load_left() -> None:
        values.append(await model.left)

    async def load_right() -> None:
        values.append(await model.right)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run_operation)
        await started.wait()
        tasks.start_soon(run_resources)
        await anyio.wait_all_tasks_blocked()
        release.set()

    assert values == [7, 7, 7]
    assert calls == 1
    assert model.left.status == model.right.status == Ready(7)


async def test_mixed_dependency_cycle_fails_instead_of_deadlocking() -> None:
    class Model(Reactive):
        @resource
        async def result(self) -> int:
            return await execution

        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            return await self.result

    model = Model()
    execution = model.work.start()
    with anyio.fail_after(1):
        with pytest.raises(ReactiveCycleError):
            await execution
    assert isinstance(execution.status, Failed)


async def test_operation_self_await_fails_instead_of_deadlocking() -> None:
    execution: OperationExecution[int, None]

    class Model:
        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            return await execution

    execution = Model().work.start()
    with anyio.fail_after(1):
        with pytest.raises(ReactiveCycleError):
            await execution
