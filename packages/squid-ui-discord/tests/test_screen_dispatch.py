"""Child results enter a fresh parent action after the child releases its lock."""

from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import override

import anyio
import pytest

from squid_reactivity.internals import CURRENT_TRANSACTION
from squid_ui import ActionEvent, Component, ComponentsV2Target, DocumentLike, state
from squid_ui.interactions import ActionMiddleware, ActionProceed, ActionRequest
from squid_ui.primitives import Button, Row, Text
from squid_ui.runtime import transaction
from squid_ui_discord import Everyone, MessageRoot, Owner
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


class Panel(Component[ComponentsV2Target]):
    value: int = state(0)

    def __init__(self, handler: Callable[[ActionEvent], Awaitable[None]] | None = None) -> None:
        self.handler = handler

    @override
    def render(self) -> DocumentLike[ComponentsV2Target]:
        return [Text(str(self.value)), Row((Button("Complete", self.complete, "complete"),))]

    async def complete(self, event: ActionEvent) -> None:
        if self.handler is not None:
            await self.handler(event)


async def test_result_uses_parent_transaction_after_child_unlock_and_preserves_message_authority() -> None:
    parent_panel = Panel()
    parent = MessageRoot(parent_panel, access=Everyone())
    await parent.send(delivered_to(message_harness(message_id=100)))
    parent_handle = parent._handle
    child_transaction: object = None
    child_panel: Panel
    child: MessageRoot
    observed: list[bool] = []

    async def completed(event: ActionEvent) -> None:
        assert isinstance(event.responder, ActionResponder)
        assert event.responder.message_root is parent
        assert parent._action_lock.locked()
        assert not child._action_lock.locked()
        assert CURRENT_TRANSACTION.get() is not None
        assert CURRENT_TRANSACTION.get() is not child_transaction
        assert child_panel.value == 1
        parent_panel.value = 2

    async def finish(event: ActionEvent) -> None:
        nonlocal child_transaction
        child_transaction = CURRENT_TRANSACTION.get()
        child_panel.value = 1
        assert isinstance(event.responder, ActionResponder)
        responder = event.responder

        async def publish() -> None:
            assert CURRENT_TRANSACTION.get() is None
            observed.append(await parent._dispatch_screen_result(responder.interaction, completed))

        responder._on_screen_success(publish)

    child_panel = Panel(finish)
    child = MessageRoot(child_panel, access=Everyone())
    await child.send(delivered_to(message_harness(message_id=200)))
    await child.dispatch("complete", interaction_harness(message_id=200))
    assert observed == [True]
    assert parent_panel.value == 2
    assert parent._handle is parent_handle
    assert not child._action_lock.locked()
    await child.finish()
    await parent.finish()


async def test_callback_failure_is_reported_and_rolls_back_parent_state() -> None:
    panel = Panel()
    root = MessageRoot(panel, access=Everyone())
    await root.send(delivered_to(message_harness()))

    async def failed(_event: ActionEvent) -> None:
        panel.value = 10
        raise ValueError("cannot accept result")

    assert not await root._dispatch_screen_result(interaction_harness(), failed)
    assert panel.value == 0
    await root.finish()


@pytest.mark.parametrize("finished", [False, True])
async def test_parent_access_and_shutdown_reject_result(finished: bool) -> None:
    root = MessageRoot(Panel(), access=Owner(2) if not finished else Everyone())
    await root.send(delivered_to(message_harness()))
    if finished:
        await root.finish()

    async def unexpected(_event: ActionEvent) -> None:
        pytest.fail("result must not be accepted")

    assert not await root._dispatch_screen_result(interaction_harness(user_id=1), unexpected)
    await root.finish()


async def test_result_rejects_nested_child_transaction() -> None:
    root = MessageRoot(Panel(), access=Everyone())

    async def unexpected(_event: ActionEvent) -> None:
        pytest.fail("result must not enter a nested transaction")

    with transaction(), pytest.raises(RuntimeError, match="after the child action commits"):
        await root._dispatch_screen_result(interaction_harness(), unexpected)
    await root.finish()


@pytest.mark.parametrize("swallow", [False, True])
async def test_middleware_cannot_turn_missing_or_failed_callback_into_success(swallow: bool) -> None:
    class Middleware(ActionMiddleware):
        async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
            if swallow:
                with suppress(ValueError):
                    await proceed()

    panel = Panel()
    root = MessageRoot(panel, access=Everyone(), middleware=(Middleware(),))
    await root.send(delivered_to(message_harness()))

    async def failed(_event: ActionEvent) -> None:
        panel.value = 10
        raise ValueError("cannot accept result")

    assert not await root._dispatch_screen_result(interaction_harness(), failed)
    assert panel.value == 0
    await root.finish()


async def test_parent_timeout_during_callback_rolls_back_without_deadlock() -> None:
    entered, release = anyio.Event(), anyio.Event()
    panel = Panel()
    root = MessageRoot(panel, access=Everyone())
    await root.send(delivered_to(message_harness()))
    accepted: list[bool] = []

    async def callback(_event: ActionEvent) -> None:
        panel.value = 10
        entered.set()
        await release.wait()

    async def dispatch() -> None:
        accepted.append(await root._dispatch_screen_result(interaction_harness(), callback))

    with anyio.fail_after(2):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch)
            await entered.wait()
            await root.handle_timeout()
            release.set()
    assert accepted == [False]
    assert panel.value == 0
    assert root._screen_close_reason == "timeout"


async def test_timeout_preserves_preselected_shutdown_reason() -> None:
    root = MessageRoot(Panel(), access=Everyone())
    root._screen_close_reason = "parent_closed"
    await root.handle_timeout()
    assert root._screen_close_reason == "parent_closed"
