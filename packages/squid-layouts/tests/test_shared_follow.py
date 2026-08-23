"""Mounts following the shared cells their renders read."""

import asyncio
import gc
import weakref
from dataclasses import dataclass

import anyio
import pytest

from squid_layouts import Component, PressEvent, Shared, TopicBus, cell, state, transaction
from squid_layouts.discord import Everyone, Mount, Reactor
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.primitives import Button, Row, Text


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int


class Workspace(Shared[Member]):
    selected: int | None = cell(None)
    detail: str = cell("")


class Panel(Component):
    show_detail: bool = state(default=False)

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def render(self):
        text = (
            f"{self.workspace.selected} {self.workspace.detail}" if self.show_detail else str(self.workspace.selected)
        )
        return [Text(text), Row((Button(label="pick", on_click=self.pick, key="pick"),))]

    async def pick(self, event: PressEvent) -> None:
        self.workspace.selected = 7


def address(workspace: Workspace, name: str) -> object:
    return (workspace, type(workspace)._cells[name])


async def drain(reactor: Reactor, bus: TopicBus) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await bus.drain()
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_two_mounts_react_once_each_to_one_commit() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    mounts = [Mount(Panel(workspace), access=Everyone(), scheduler=reactor) for _ in range(2)]
    refreshes: dict[str, int] = {}
    for mount in mounts:
        await mount.send(delivered_to(fake_message()))
        assert mount.followed == (address(workspace, "selected"),)

        def counted(mount: Mount = mount):
            async def refresh_now(*, links=()) -> None:
                refreshes[mount.id] = refreshes.get(mount.id, 0) + 1

            return refresh_now

        mount.refresh_now = counted()  # pyrefly: ignore

    with transaction():
        workspace.selected = 3
        workspace.selected = 4
    await drain(reactor, bus)

    assert refreshes == {mounts[0].id: 1, mounts[1].id: 1}


async def test_a_dropped_conditional_read_stops_refreshing() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    panel = Panel(workspace)
    panel.show_detail = True
    mount = Mount(panel, access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))
    assert set(mount.followed) == {address(workspace, "selected"), address(workspace, "detail")}

    panel.show_detail = False
    await mount.refresh_now()
    assert mount.followed == (address(workspace, "selected"),)
    assert {topic.topic for topic in bus.snapshot().topics} == {address(workspace, "selected")}


async def test_a_discarded_staged_render_leaves_no_permanent_follow() -> None:
    """Over-subscribe, never under-subscribe: a failed delivery's follow is dropped next render."""
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    panel = Panel(workspace)
    mount = Mount(panel, access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))

    panel.show_detail = True
    candidate = mount._stage()
    assert set(mount.followed) == {address(workspace, "selected"), address(workspace, "detail")}
    mount._rollback(candidate)

    panel.show_detail = False
    await mount.refresh_now()
    assert mount.followed == (address(workspace, "selected"),)


async def test_no_follow_outlives_its_mount() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    mount = Mount(Panel(workspace), access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))
    assert bus.snapshot().topics != ()

    await mount.finish(disable=False)
    assert mount.followed == ()
    assert bus.snapshot().topics == ()


async def test_a_namespace_dropped_by_its_last_mount_is_collected() -> None:
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    gone = weakref.ref(workspace)
    mount = Mount(Panel(workspace), access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))
    await mount.finish(disable=False)

    del workspace, mount
    gc.collect()
    assert gone() is None


async def test_a_scheduler_that_cannot_follow_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    bus = TopicBus()
    workspace = Workspace(bus, Member(1))
    mount = Mount(Panel(workspace), access=Everyone())
    with caplog.at_level("WARNING"):
        await mount.send(delivered_to(fake_message()))
        await mount.refresh_now()
    assert sum("cannot follow topics" in record.message for record in caplog.records) == 1
