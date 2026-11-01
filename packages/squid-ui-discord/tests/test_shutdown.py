"""Accepted closure belongs to the host; observers share its terminal result."""

import asyncio
from typing import override

import anyio
import pytest

from squid_ui import Component, ComponentsV2Target
from squid_ui.primitives import Text
from squid_ui.runtime import UIExecutionHost
from squid_ui_discord import Everyone, MessageRoot, SessionManager
from squid_ui_discord.sessions import Opened
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


class Panel(Component[ComponentsV2Target]):
    @override
    def render(self) -> Text:
        return Text("panel")


async def test_cancel_before_acceptance_leaves_panel_open() -> None:
    root = MessageRoot(Panel(), access=Everyone())
    await root._render_lock.acquire()
    try:
        with anyio.move_on_after(0):
            await root.finish(wait=False)
        assert not root.finished
    finally:
        root._render_lock.release()
    await root.finish()


async def test_unavailable_host_rejects_closure_before_changing_panel() -> None:
    for host in (None, UIExecutionHost()):
        root = MessageRoot(Panel(), access=Everyone(), execution_host=host)
        with pytest.raises(RuntimeError, match="running UIExecutionHost"):
            await root.finish(wait=False)
        assert not root.finished


async def test_cancelled_waiter_leaves_cleanup_owned_and_repeatable() -> None:
    root = MessageRoot(Panel(), access=Everyone())
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def callback(_root: MessageRoot) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()

    root.on_finish(callback)
    await root.finish(wait=False)
    assert root.finished
    await entered.wait()
    try:
        with anyio.move_on_after(0):
            await root.wait()
        assert not root._shutdown.done.is_set()
        await root.finish(wait=False)
    finally:
        release.set()
    await root.wait()
    await root.finish()
    assert calls == 1


async def test_callbacks_all_run_and_every_waiter_observes_same_failure() -> None:
    root = MessageRoot(Panel(), access=Everyone())
    events: list[str] = []

    async def broken(_root: MessageRoot) -> None:
        events.append("broken")
        raise ValueError("callback failed")

    async def remaining(_root: MessageRoot) -> None:
        events.append("remaining")

    root.on_finish(broken)
    root.on_finish(remaining)
    await root.finish(wait=False)
    failures: list[ExceptionGroup] = []

    async def observe() -> None:
        with pytest.raises(ExceptionGroup) as failure:
            await root.wait()
        failures.append(failure.value)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(observe)
        tasks.start_soon(observe)
    await observe()
    assert all(error is failures[0] for error in failures)
    assert failures[0].subgroup(ValueError) is not None
    assert events == ["broken", "remaining"]


@pytest.mark.parametrize("child_task", [False, True])
async def test_callback_cannot_wait_for_itself(child_task: bool) -> None:
    root = MessageRoot(Panel(), access=Everyone())

    async def observe() -> None:
        with pytest.raises(RuntimeError, match="own completion"):
            await root.wait()
        await root.finish(wait=False)

    async def callback(_root: MessageRoot) -> None:
        if child_task:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(observe)
        else:
            await observe()

    root.on_finish(callback)
    await root.finish()


@pytest.mark.parametrize("method", ["finish", "finish_via", "dismiss", "timeout"])
async def test_terminal_paths_share_one_completion(method: str) -> None:
    root = MessageRoot(Panel(), access=Everyone())
    await root.send(delivered_to(message_harness()))
    seen: list[str] = []

    async def callback(_root: MessageRoot) -> None:
        seen.append("finished")

    root.on_finish(callback)
    if method == "finish_via":
        await root.finish_via(interaction_harness())
    elif method == "dismiss":
        await root.dismiss(wait=False)
    elif method == "timeout":
        await root.handle_timeout()
    else:
        await root.finish(wait=False)
    await root.wait()
    await root.dismiss()
    assert seen == ["finished"]


async def test_session_leaves_registry_before_child_cleanup_finishes() -> None:
    manager = SessionManager()
    root = MessageRoot(Panel(), access=Everyone())
    opened = await manager.open(root, delivered_to(message_harness()), key="editor")
    assert isinstance(opened, Opened)
    session = opened.session
    child = MessageRoot(Panel(), access=Everyone())
    await session.attach(child, delivered_to(message_harness()))
    entered, release = asyncio.Event(), asyncio.Event()
    events: list[str] = []

    async def child_finished(_root: MessageRoot) -> None:
        entered.set()
        await release.wait()
        events.append("child")
        raise ValueError("child callback failed")

    async def parent_finished(_root: MessageRoot) -> None:
        events.append("parent")

    child.on_finish(child_finished)
    root.on_finish(parent_finished)
    await session.finish(wait=False)
    assert manager.session_for(root) is None
    assert manager.session_for(child) is None
    await entered.wait()
    try:
        assert events == []
    finally:
        release.set()
    with pytest.raises(ExceptionGroup) as failure:
        await session.wait()
    assert failure.value.subgroup(ValueError) is not None
    assert events == ["child", "parent"]
    with pytest.raises(ExceptionGroup) as repeated:
        await session.finish()
    assert repeated.value is failure.value


async def test_host_close_drains_accepted_shutdown_before_returning() -> None:
    async with anyio.create_task_group() as tasks:
        host = UIExecutionHost()
        await tasks.start(host.run)
        root = MessageRoot(Panel(), access=Everyone(), execution_host=host)
        entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def callback(_root: MessageRoot) -> None:
            entered.set()
            await release.wait()

        async def close_host() -> None:
            await host.close()
            closed.set()

        root.on_finish(callback)
        await root.finish(wait=False)
        await entered.wait()
        tasks.start_soon(close_host)
        await anyio.lowlevel.checkpoint()
        try:
            assert not closed.is_set()
        finally:
            release.set()
        await closed.wait()
        await root.wait()


async def test_cancelling_initiator_after_acceptance_does_not_cancel_shutdown() -> None:
    root = MessageRoot(Panel(), access=Everyone())
    entered, release, caller_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    scope = anyio.CancelScope()

    async def callback(_root: MessageRoot) -> None:
        entered.set()
        await release.wait()

    async def finish() -> None:
        with scope:
            await root.finish()
        caller_done.set()

    root.on_finish(callback)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(finish)
        await entered.wait()
        scope.cancel()
        await caller_done.wait()
        try:
            assert root.finished
            assert not root._shutdown.done.is_set()
        finally:
            release.set()
        await root.wait()


async def test_parent_waits_for_child_that_was_already_closing() -> None:
    manager = SessionManager()
    root = MessageRoot(Panel(), access=Everyone())
    opened = await manager.open(root, delivered_to(message_harness()))
    assert isinstance(opened, Opened)
    child = MessageRoot(Panel(), access=Everyone())
    await opened.session.attach(child, delivered_to(message_harness()))
    entered, release = asyncio.Event(), asyncio.Event()
    events: list[str] = []

    async def child_callback(_root: MessageRoot) -> None:
        entered.set()
        await release.wait()
        with pytest.raises(RuntimeError, match="own completion"):
            await root.wait()
        events.append("child")

    async def parent_callback(_root: MessageRoot) -> None:
        events.append("parent")

    child.on_finish(child_callback)
    root.on_finish(parent_callback)
    await child.finish(wait=False)
    await entered.wait()
    await root.finish(wait=False)
    release.set()
    await root.wait()
    assert events == ["child", "parent"]


async def test_failed_child_cleanup_releases_parent_dependencies() -> None:
    manager = SessionManager()
    root = MessageRoot(Panel(), access=Everyone())
    opened = await manager.open(root, delivered_to(message_harness()))
    assert isinstance(opened, Opened)
    child = MessageRoot(Panel(), access=Everyone())
    await opened.session.attach(child, delivered_to(message_harness()))
    pause, release = asyncio.Event(), asyncio.Event()

    async def fail(_root: MessageRoot) -> None:
        pause.set()
        await release.wait()
        raise ValueError("child cleanup failed")

    child.on_finish(fail)
    with anyio.fail_after(3):
        await child.finish(wait=False)
        await pause.wait()
        assert child in root._shutdown_dependencies
        assert child in opened.session._retiring
        release.set()
        with pytest.raises(ExceptionGroup):
            await child.wait()

    assert child not in root._shutdown_dependencies
    assert child not in opened.session._retiring
    await root.finish()
