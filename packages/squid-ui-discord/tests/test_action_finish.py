"""Action closure is accepted after commit and cannot roll back on cleanup failure."""

import asyncio
from typing import override

import anyio
import pytest

from squid_ui import ActionEvent, Component, ComponentsV2Target, DocumentLike, state
from squid_ui.forms import FormSpec, TextField
from squid_ui.primitives import Button, Row, Text
from squid_ui.runtime import transaction
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.sessions import Opened, SessionManager
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


@pytest.mark.parametrize("submit", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_action_commits_before_terminal_render_and_callbacks(submit: bool, cleanup_fails: bool) -> None:
    rendered: list[int] = []
    observed: list[int] = []
    events: list[ActionEvent] = []

    class Panel(Component[ComponentsV2Target]):
        value: int = state(0)

        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            rendered.append(self.value)
            return [Text(str(self.value)), Row((Button(label="Close", key="close", on_click=self.close),))]

        async def close(self, event: ActionEvent) -> None:
            events.append(event)
            self.value = 1
            event.finish_on_success()
            assert not root.finished
            self.value = 2

    panel = Panel()
    root = MessageRoot(panel, access=Everyone())
    await root.send(delivered_to(message_harness()))

    async def finished(_root: MessageRoot) -> None:
        observed.append(panel.value)
        if cleanup_fails:
            raise ValueError("cleanup failed")

    root.on_finish(finished)

    async def dispatch() -> None:
        if submit:
            await root.dispatch_submit(
                "close",
                interaction_harness(),
                FormSpec("Close", (TextField(key="name", label="Name"),)),
                {"name": "test"},
                panel.close,
            )
        else:
            await root.dispatch("close", interaction_harness())

    if cleanup_fails:
        with pytest.raises((ExceptionGroup, ValueError)):
            await dispatch()
    else:
        await dispatch()
    assert root.finished
    assert panel.value == 2
    assert rendered[-1] == 2
    assert observed == [2]
    with pytest.raises(RuntimeError, match="only available during its action handler"):
        events[0].finish_on_success()


@pytest.mark.parametrize("cancel", [False, True])
async def test_unsuccessful_action_discards_closure_request(cancel: bool) -> None:
    requested = asyncio.Event()
    scope = anyio.CancelScope()
    failures: list[Exception] = []

    class Panel(Component[ComponentsV2Target]):
        value: int = state(0)

        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            return Row((Button(label="Close", key="close", on_click=self.close),))

        async def close(self, event: ActionEvent) -> None:
            self.value = 1
            event.finish_on_success()
            requested.set()
            if cancel:
                await anyio.sleep_forever()
            raise ValueError("handler failed")

    async def on_error(interaction: object, error: Exception, source: str) -> None:
        del interaction, source
        failures.append(error)

    panel = Panel()
    root = MessageRoot(panel, access=Everyone(), on_error=on_error)
    await root.send(delivered_to(message_harness()))

    async def dispatch() -> None:
        with scope:
            await root.dispatch("close", interaction_harness())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(dispatch)
        await requested.wait()
        if cancel:
            scope.cancel()
    assert panel.value == 0
    assert not root.finished
    assert bool(failures) is not cancel
    await root.finish()


@pytest.mark.parametrize("operation", ["finish", "finish_nowait", "finish_via", "dismiss", "session"])
async def test_immediate_closure_rejects_transactions_before_acceptance(operation: str) -> None:
    rendered: list[int] = []
    observed: list[int] = []

    class Panel(Component[ComponentsV2Target]):
        value: int = state(0)

        @override
        def render(self) -> Text:
            rendered.append(self.value)
            return Text(str(self.value))

    panel = Panel()
    root = MessageRoot(panel, access=Everyone())
    await root.send(delivered_to(message_harness()))

    async def callback(_root: MessageRoot) -> None:
        observed.append(panel.value)

    root.on_finish(callback)
    manager = SessionManager()
    opened = await manager.open(root, delivered_to(message_harness()))
    assert isinstance(opened, Opened)
    with transaction():
        panel.value = 1
        with pytest.raises(RuntimeError, match=r"use event\.finish_on_success"):
            match operation:
                case "finish":
                    await root.finish()
                case "finish_nowait":
                    await root.finish(wait=False)
                case "finish_via":
                    await root.finish_via(interaction_harness())
                case "dismiss":
                    await root.dismiss()
                case "session":
                    await opened.session.finish()
        assert not root.finished
        assert observed == []
        assert rendered[-1] == 0
    await root.finish()
    assert observed == [1]
    assert panel.value == 1
