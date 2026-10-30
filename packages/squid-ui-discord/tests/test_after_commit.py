"""Committed actions supervise operations without replaying them through history."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import override

import anyio
import pytest

from squid_reactivity.actions import ActionLedger, OperationEventSnapshot, action_result_sink
from squid_reactivity.internals import CURRENT_TRANSACTION
from squid_reactivity.operations import Failed, Pending, Succeeded
from squid_ui import ActionEvent, Component, ComponentsV2Target, DocumentLike, state
from squid_ui.interactions import ActionMode
from squid_ui.primitives import Button, Row, Text
from squid_ui.runtime import (
    ActionDiscardedError,
    Discarded,
    History,
    LocalTopicBus,
    PostCommitOperation,
    ServiceHost,
    SharedState,
    history,
    transaction,
)
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


class Panel(Component[ComponentsV2Target]):
    value: int = state(0)
    history: History = history()

    def __init__(self, handler: Callable[[Panel, ActionEvent], Awaitable[None]]) -> None:
        self._handler = handler

    @override
    def render(self) -> DocumentLike[ComponentsV2Target]:
        return [Text(str(self.value)), Row((Button(label="Save", key="save", on_click=self.save),))]

    async def save(self, event: ActionEvent) -> None:
        await self._handler(self, event)


async def mount(handler: Callable[[Panel, ActionEvent], Awaitable[None]]) -> tuple[Panel, MessageRoot]:
    panel = Panel(handler)
    root = MessageRoot(panel, access=Everyone())
    await root.send(delivered_to(message_harness()))
    return panel, root


async def test_operation_observes_commit_and_shares_history_causality_without_replaying() -> None:
    handles: list[PostCommitOperation[int]] = []
    events: list[ActionEvent] = []
    observed: list[int] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        events.append(event)
        panel.history.record("Save")
        panel.value = 1

        async def callback() -> int:
            assert CURRENT_TRANSACTION.get() is None
            observed.append(panel.value)
            return 42

        operation = event.on_success(callback, name="Observe saved value")
        handles.append(operation)
        assert operation.context is None
        assert isinstance(operation.status, Pending)
        with pytest.raises(RuntimeError, match="outside the action transaction"):
            await operation.wait()
        panel.value = 2
        assert observed == []

    panel, root = await mount(handler)
    ledger = ActionLedger()
    with action_result_sink(ledger):
        await root.dispatch("save", interaction_harness())
        operation = handles[0]
        assert await operation == 42
        assert await operation.wait() == 42
        assert isinstance(operation.status, Succeeded)
        assert observed == [2]
        assert operation.context is not None
        commit = panel.history.entries[0].original_commit
        assert commit is not None
        assert operation.context.cause == commit.context.causal_ref()
        emitted = [item for item in ledger.events if isinstance(item, OperationEventSnapshot)]
        assert [item.status for item in emitted] == ["started", "succeeded"]
        assert (await panel.history.undo()).applied
        assert panel.value == 0
        assert (await panel.history.redo()).applied
        assert panel.value == 2
        assert observed == [2]
    with pytest.raises(RuntimeError, match="only available during its action handler"):
        events[0].on_success(lambda: root.finish())
    await root.finish()


@pytest.mark.parametrize("cancel", [False, True])
async def test_rollback_discards_work_and_settles_its_handle(cancel: bool) -> None:
    handles: list[PostCommitOperation[None]] = []
    requested = asyncio.Event()
    ran: list[bool] = []
    scope = anyio.CancelScope()

    async def callback() -> None:
        ran.append(True)

    async def handler(panel: Panel, event: ActionEvent) -> None:
        panel.value = 1
        handles.append(event.on_success(callback))
        requested.set()
        if cancel:
            await anyio.sleep_forever()
        raise ValueError("handler failed")

    panel, root = await mount(handler)

    async def dispatch() -> None:
        with scope:
            await root.dispatch("save", interaction_harness())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(dispatch)
        await requested.wait()
        if cancel:
            scope.cancel()
    assert panel.value == 0
    assert ran == []
    assert isinstance(handles[0].status, Discarded)
    assert handles[0].context is None
    with pytest.raises(ActionDiscardedError, match="rolled back"):
        await handles[0].wait()
    await root.finish()


async def test_failure_preserves_commit_and_does_not_skip_other_operations() -> None:
    handles: list[PostCommitOperation[None]] = []
    observed: list[int] = []
    failure = ValueError("operation failed")

    async def handler(panel: Panel, event: ActionEvent) -> None:
        panel.value = 1

        async def fail() -> None:
            raise failure

        async def succeed() -> None:
            observed.append(panel.value)

        handles.extend((event.on_success(fail), event.on_success(succeed)))

    panel, root = await mount(handler)
    with pytest.raises(ValueError, match="operation failed"):
        await root.dispatch("save", interaction_harness())
    assert panel.value == 1
    assert observed == [1]
    assert isinstance(handles[0].status, Failed)
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            await handles[0].wait()
        assert caught.value is failure
    await root.finish()


async def test_cancelled_dispatch_does_not_cancel_operation_or_other_waiters() -> None:
    entered, release, caller_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    scope = anyio.CancelScope()
    handles: list[PostCommitOperation[int]] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        panel.value = 1

        async def callback() -> int:
            entered.set()
            await release.wait()
            return 7

        handles.append(event.on_success(callback))

    panel, root = await mount(handler)

    async def dispatch() -> None:
        with scope:
            await root.dispatch("save", interaction_harness())
        caller_done.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(dispatch)
        await entered.wait()
        scope.cancel()
        await caller_done.wait()
        try:
            assert panel.value == 1
            assert isinstance(handles[0].status, Pending)
        finally:
            release.set()
        assert await handles[0].wait() == 7
    await root.finish()


async def test_self_wait_is_rejected_in_operation_and_its_children() -> None:
    handles: list[PostCommitOperation[None]] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        async def check() -> None:
            with pytest.raises(RuntimeError, match="own completion"):
                await handles[0].wait()

        async def callback() -> None:
            await check()
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(check)

        handles.append(event.on_success(callback))

    _, root = await mount(handler)
    await root.dispatch("save", interaction_harness())
    await root.finish()


async def test_readonly_action_can_schedule_work_with_its_dispatch_identity() -> None:
    handles: list[PostCommitOperation[None]] = []
    observed: list[bool] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        async def callback() -> None:
            observed.append(True)

        handles.append(event.on_success(callback))

    class ReadPanel(Panel):
        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            return Row((Button(label="Read", key="save", on_click=self.save, mode=ActionMode.PARALLEL_READ),))

    root = MessageRoot(ReadPanel(handler), access=Everyone())
    await root.send(delivered_to(message_harness()))
    ledger = ActionLedger()
    with action_result_sink(ledger):
        await root.dispatch("save", interaction_harness())
    assert observed == [True]
    assert handles[0].context is not None
    assert str(handles[0].context.root_action_id) == ledger.results[0].root_action_id
    assert handles[0].context.cause is not None
    assert handles[0].context.cause.identity == ledger.results[0].action_id
    await root.finish()


async def test_commit_conflict_discards_operation() -> None:
    class Counter(SharedState[str]):
        value: int = state(0)

    counter = Counter(LocalTopicBus(), "test")
    requested, release = asyncio.Event(), asyncio.Event()
    handles: list[PostCommitOperation[None]] = []
    ran: list[bool] = []

    async def callback() -> None:
        ran.append(True)

    async def handler(panel: Panel, event: ActionEvent) -> None:
        counter.value += 1
        panel.value = 1
        handles.append(event.on_success(callback))
        requested.set()
        await release.wait()

    panel, root = await mount(handler)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(root.dispatch, "save", interaction_harness(), None)
        await requested.wait()
        counter.value = 5
        release.set()
    assert counter.value == 5
    assert panel.value == 0
    assert ran == []
    assert isinstance(handles[0].status, Discarded)
    await root.finish()


async def test_nested_dispatch_cannot_attach_work_to_its_outer_transaction() -> None:
    ran: list[bool] = []

    async def callback() -> None:
        ran.append(True)

    async def handler(panel: Panel, event: ActionEvent) -> None:
        event.on_success(callback)

    _, root = await mount(handler)
    with transaction():
        await root.dispatch("save", interaction_harness())
    await anyio.lowlevel.checkpoint()
    assert ran == []
    await root.finish()


async def test_host_close_drains_accepted_operations() -> None:
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    handles: list[PostCommitOperation[None]] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        async def callback() -> None:
            entered.set()
            await release.wait()

        handles.append(event.on_success(callback))

    async with anyio.create_task_group() as tasks:
        host = ServiceHost()
        await tasks.start(host.run)
        root = MessageRoot(Panel(handler), access=Everyone(), service_host=host)
        await root.send(delivered_to(message_harness()))

        async def close() -> None:
            await host.close()
            closed.set()

        tasks.start_soon(root.dispatch, "save", interaction_harness(), None)
        await entered.wait()
        await root.finish(wait=False)
        tasks.start_soon(close)
        await anyio.lowlevel.checkpoint()
        try:
            assert not closed.is_set()
        finally:
            release.set()
        await closed.wait()
        await handles[0].wait()
        await root.wait()


async def test_cancelled_operation_does_not_cancel_the_dispatcher_or_other_work() -> None:
    observed: list[bool] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        panel.value = 1

        async def cancel() -> None:
            raise asyncio.CancelledError

        async def complete() -> None:
            observed.append(True)

        event.on_success(cancel)
        event.on_success(complete)

    panel, root = await mount(handler)
    with pytest.raises(RuntimeError, match="operation was cancelled"):
        await root.dispatch("save", interaction_harness())
    assert panel.value == 1
    assert observed == [True]
    await root.finish()


async def test_failed_host_submission_settles_handle_without_rolling_back_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles: list[PostCommitOperation[None]] = []
    ran: list[bool] = []

    async def handler(panel: Panel, event: ActionEvent) -> None:
        panel.value = 1

        async def callback() -> None:
            ran.append(True)

        handles.append(event.on_success(callback))

    panel, root = await mount(handler)
    assert root.config.service_host is not None

    def reject(callback: Callable[[], Awaitable[None]]) -> None:
        raise RuntimeError("host submission failed")

    with monkeypatch.context() as patch:
        patch.setattr(root.config.service_host, "start_cleanup", reject)
        with pytest.raises(RuntimeError, match="host submission failed"):
            await root.dispatch("save", interaction_harness())
    assert panel.value == 1
    assert ran == []
    assert isinstance(handles[0].status, Failed)
    with pytest.raises(RuntimeError, match="host submission failed"):
        await handles[0].wait()
    await root.finish()


async def test_rendering_status_does_not_take_ownership_of_pending_operation() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    handles: list[PostCommitOperation[None]] = []

    class StatusPanel(Panel):
        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            return [
                Text(type(handles[0].status).__name__ if handles else "Unscheduled"),
                Row((Button(label="Save", key="save", on_click=self.save),)),
            ]

    async def handler(panel: Panel, event: ActionEvent) -> None:
        async def callback() -> None:
            entered.set()
            await release.wait()

        handles.append(event.on_success(callback))

    root = MessageRoot(StatusPanel(handler), access=Everyone())
    await root.send(delivered_to(message_harness()))
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(root.dispatch, "save", interaction_harness(), None)
        await entered.wait()
        try:
            with anyio.fail_after(1):
                await root.refresh()
            assert isinstance(handles[0].status, Pending)
        finally:
            release.set()
    await root.finish()
