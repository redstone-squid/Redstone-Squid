import asyncio

import anyio
import pytest

from squid_reactive.operations import Cancelled, Failed, Pending, Progress, Succeeded, operation


class Owner:
    def __init__(self) -> None:
        self.invalidations = 0
        self.calls = 0

    def invalidate(self) -> None:
        self.invalidations += 1

    @operation(initial="starting")
    async def work(self, progress: Progress[str]) -> int:
        self.calls += 1
        progress.set("working")
        return 42


async def test_operation_reports_progress_and_succeeds_once() -> None:
    owner = Owner()

    assert owner.work.status == Pending("starting")
    assert await owner.work == 42
    assert owner.work.status == Succeeded(42)
    assert await owner.work == 42
    assert owner.calls == 1
    assert owner.invalidations == 2


async def test_operation_joins_one_in_flight_attempt() -> None:
    started = asyncio.Event()
    resume = asyncio.Event()

    class SlowOwner(Owner):
        @operation(initial=None)
        async def slow(self, progress: Progress[None]) -> str:
            self.calls += 1
            started.set()
            await resume.wait()
            return "done"

    owner = SlowOwner()
    values: list[str] = []

    async def run() -> None:
        values.append(await owner.slow)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run)
        await started.wait()
        tasks.start_soon(run)
        resume.set()

    assert values == ["done", "done"]
    assert owner.calls == 1


async def test_operation_failure_is_terminal() -> None:
    class FailingOwner(Owner):
        @operation(initial="starting")
        async def failing(self, progress: Progress[str]) -> None:
            progress.set("almost")
            raise ValueError("nope")

    owner = FailingOwner()

    with pytest.raises(ValueError, match="nope"):
        await owner.failing
    match owner.failing.status:
        case Failed(error=error, progress="almost"):
            assert str(error) == "nope"
        case status:
            pytest.fail(f"unexpected operation status: {status!r}")
    with pytest.raises(ValueError, match="nope"):
        await owner.failing


async def test_operation_cancellation_is_terminal() -> None:
    class CancelledOwner(Owner):
        @operation(initial="waiting")
        async def waiting(self, progress: Progress[str]) -> None:
            await anyio.sleep_forever()

    owner = CancelledOwner()

    with anyio.move_on_after(0.01):
        await owner.waiting

    assert owner.waiting.status == Cancelled("waiting")
    with pytest.raises(asyncio.CancelledError):
        await owner.waiting
