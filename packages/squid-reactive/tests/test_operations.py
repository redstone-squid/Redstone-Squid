import asyncio

import anyio
import pytest

from squid_reactive import (
    ActionContext,
    ActionLedger,
    OperationEventSnapshot,
    Reactive,
    ResourceEventSnapshot,
    action_scope,
    add_action_result_sink,
    state,
    transaction,
)
from squid_reactive.operations import Cancelled, Failed, Pending, Progress, Succeeded, operation
from squid_reactive.resources import resource


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
    execution = owner.work.start()

    assert execution.status == Pending("starting")
    assert await execution == 42
    assert execution.status == Succeeded(42)
    assert await execution == 42
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
    execution = owner.slow.start()
    values: list[str] = []

    async def run() -> None:
        values.append(await execution)

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
    execution = owner.failing.start()

    with pytest.raises(ValueError, match="nope"):
        await execution
    match execution.status:
        case Failed(error=error, progress="almost"):
            assert str(error) == "nope"
        case status:
            pytest.fail(f"unexpected operation status: {status!r}")
    with pytest.raises(ValueError, match="nope"):
        await execution


async def test_operation_cancellation_is_terminal() -> None:
    class CancelledOwner(Owner):
        @operation(initial="waiting")
        async def waiting(self, progress: Progress[str]) -> None:
            await anyio.sleep_forever()

    owner = CancelledOwner()
    execution = owner.waiting.start()

    with anyio.move_on_after(0.01):
        await execution

    assert execution.status == Cancelled("waiting")
    with pytest.raises(asyncio.CancelledError):
        await execution


async def test_each_start_has_fresh_identity_and_retry_state() -> None:
    owner = Owner()
    first = owner.work.start()
    second = owner.work.start()

    assert first.context.execution_id != second.context.execution_id
    assert await first == 42
    assert await second == 42
    assert owner.calls == 2


async def test_operation_start_and_terminal_state_form_causal_ledger_nodes() -> None:
    owner = Owner()
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    action = ActionContext.create("publish")
    try:
        with action_scope(action):
            execution = owner.work.start()
        await execution
    finally:
        ledger.close()

    events = [event for event in ledger.events if isinstance(event, OperationEventSnapshot)]
    assert [event.status for event in events] == ["started", "succeeded"]
    assert {event.execution_id for event in events} == {str(execution.context.execution_id)}
    assert events[0].cause == action.causal_ref()
    assert events[0].root_action_id == str(action.root_action_id)


async def test_operation_completion_publishes_state_as_a_fresh_caused_action() -> None:
    class StatefulOwner(Reactive):
        value: int = state(0)

        def invalidate(self) -> None:
            pass

        @operation(initial=None)
        async def work(self, progress: Progress[None]) -> int:
            return 42

    owner = StatefulOwner()
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    execution = owner.work.start()
    try:
        result = await execution
        with execution.start_action("publish result"):
            owner.value = result
    finally:
        ledger.close()

    assert owner.value == 42
    result = ledger.results[0]
    assert result.cause == execution.context.causal_ref()
    assert result.root_action_id == str(result.action_id)


async def test_action_operation_response_and_resource_generation_form_one_graph() -> None:
    class GraphOwner(Reactive):
        value: int = state(0)

        def invalidate(self) -> None:
            pass

        @operation(initial=None)
        async def fetch(self, progress: Progress[None]) -> int:
            return 42

        @resource
        async def rendered(self) -> str:
            return str(self.value)

    owner = GraphOwner()
    root = ActionContext.create("click")
    ledger = ActionLedger(limit=20)
    add_action_result_sink(ledger)
    try:
        with transaction(action_context=root):
            execution = owner.fetch.start()
        result = await execution
        with execution.start_action("publish response") as response:
            owner.value = result
            owner.rendered.invalidate()
        assert await owner.rendered == "42"
    finally:
        ledger.close()

    operation_event = next(event for event in ledger.events if isinstance(event, OperationEventSnapshot))
    resource_event = next(event for event in ledger.events if isinstance(event, ResourceEventSnapshot))
    response_outcome = next(result for result in ledger.results if result.name == "publish response")
    assert operation_event.cause == root.causal_ref()
    assert response_outcome.cause == execution.context.causal_ref()
    assert resource_event.cause is not None and resource_event.cause.identity == str(response.action_id)
    assert {operation_event.root_action_id, response_outcome.root_action_id, resource_event.root_action_id} == {
        str(root.action_id)
    }
