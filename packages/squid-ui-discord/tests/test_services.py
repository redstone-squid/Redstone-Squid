"""Service ownership crosses Discord's delivery/commit and asynchronous teardown boundaries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import override
from unittest.mock import AsyncMock

import anyio
import pytest
from anyio.abc import TaskStatus

from squid_ui import Component, ComponentsV2Target, DocumentLike, state
from squid_ui.primitives import Text
from squid_ui.runtime import ManagedService, ServiceHost
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


@asynccontextmanager
async def running_host() -> AsyncIterator[ServiceHost]:
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            host = ServiceHost()
            await tasks.start(host.run)
            try:
                yield host
            finally:
                await host.close()


class Activity(ManagedService):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.fail = asyncio.Event()
        self.root: MessageRoot | None = None

    @override
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self.worker)
                self.started.set()
                await self.setup()
                task_status.started()
                await anyio.sleep_forever()
        finally:
            with anyio.CancelScope(shield=True):
                await self.cleanup()

    async def setup(self) -> None:
        pass

    async def worker(self) -> None:
        await self.fail.wait()
        raise ValueError("service worker failed")

    async def cleanup(self) -> None:
        await anyio.sleep(0)
        if self.root is not None:
            assert not self.root._render_lock.locked()
        self.stopped.set()


class Panel(Component[ComponentsV2Target]):
    def __init__(self, service: Activity | None = None) -> None:
        self.service = self.own(Activity() if service is None else service)

    @override
    def render(self) -> Text:
        return Text("preview")


async def test_failed_delivery_never_activates_and_retry_then_finish_cleans_up() -> None:
    async with running_host() as host:
        panel = Panel()
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = root
        with pytest.raises(ValueError, match="delivery failed"):
            await root.send(AsyncMock(side_effect=ValueError("delivery failed")))
        assert not panel.service.started.is_set()
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()
        await root.finish()
        assert panel.service.stopped.is_set()
        await root.finish()


async def test_removing_owner_waits_for_cleanup_but_keeps_message_root_live() -> None:
    async with running_host() as host:
        panel = Panel()

        class Root(Component[ComponentsV2Target]):
            visible: bool = state(default=True)

            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                return self.boundary(panel, key="editor") if self.visible else Text("closed")

        component = Root()
        root = MessageRoot(component, access=Everyone(), service_host=host)
        panel.service.root = root
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()
        component.visible = False
        await root.refresh()
        assert panel.service.stopped.is_set()
        assert not root.finished
        await root.finish()


@pytest.mark.parametrize("during_setup", [False, True])
async def test_unexpected_failure_finishes_only_its_message_root(during_setup: bool) -> None:
    async with running_host() as host:

        class Broken(Activity):
            @override
            async def setup(self) -> None:
                await super().setup()
                if during_setup:
                    raise ValueError("setup failed")

        panel = Panel(Broken())
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = root
        sibling = Panel()
        sibling_root = MessageRoot(sibling, access=Everyone(), service_host=host)
        finished = asyncio.Event()

        async def on_finish(_root: MessageRoot) -> None:
            assert panel.service.stopped.is_set()
            finished.set()

        root.on_finish(on_finish)
        await sibling_root.send(delivered_to(message_harness()))
        await sibling.service.started.wait()
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()
        panel.service.fail.set()
        await finished.wait()
        assert root.finished
        assert not sibling_root.finished
        assert not sibling.service.stopped.is_set()
        await sibling_root.finish()


async def test_failed_disable_edit_still_completes_service_cleanup() -> None:
    async with running_host() as host:
        panel = Panel()
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = root
        message = message_harness()
        await root.send(delivered_to(message))
        await panel.service.started.wait()
        message.edit = AsyncMock(side_effect=ValueError("edit failed"))
        with pytest.raises(ValueError, match="edit failed"):
            await root.finish()
        assert panel.service.stopped.is_set()
        assert root.finished


async def test_concurrent_finish_callers_wait_for_same_cleanup() -> None:
    async with running_host() as host:
        entered, release = asyncio.Event(), asyncio.Event()
        finished: list[str] = []

        class Slow(Activity):
            @override
            async def cleanup(self) -> None:
                entered.set()
                await release.wait()
                await super().cleanup()

        panel = Panel(Slow())
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = root
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()

        async def finish(label: str) -> None:
            await root.finish(disable=False)
            assert panel.service.stopped.is_set()
            finished.append(label)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(finish, "first")
            await entered.wait()
            tasks.start_soon(finish, "second")
            await anyio.sleep(0)
            assert not finished
            release.set()
        assert set(finished) == {"first", "second"}


async def test_multiple_cleanups_can_reenter_frontend_without_waiting_on_each_other() -> None:
    async with running_host() as host:

        class Reentrant(Activity):
            @override
            async def cleanup(self) -> None:
                assert self.root is not None
                await self.root.refresh()
                await super().cleanup()

        class TwoServices(Panel):
            def __init__(self) -> None:
                super().__init__(Reentrant())
                self.second = self.own(Reentrant())

        panel = TwoServices()
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = panel.second.root = root
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()
        await panel.second.started.wait()
        await root.finish(disable=False)
        assert panel.service.stopped.is_set()
        assert panel.second.stopped.is_set()


async def test_failed_terminal_render_and_teardown_callback_still_stop_services() -> None:
    async with running_host() as host:

        class BrokenRender(Panel):
            broken: bool = state(default=False)

            @override
            def render(self) -> Text:
                if self.broken:
                    raise ValueError("terminal render failed")
                return super().render()

        panel = BrokenRender()
        root = MessageRoot(panel, access=Everyone(), service_host=host)
        panel.service.root = root
        await root.send(delivered_to(message_harness()))
        await panel.service.started.wait()

        def broken_expiry_cleanup() -> None:
            raise ValueError("expiry cleanup failed")

        root._unwatch_expiry = broken_expiry_cleanup
        panel.broken = True
        with pytest.raises(ValueError, match="terminal render failed"):
            await root.finish_via(interaction_harness())
        assert root.finished
        assert panel.service.stopped.is_set()
