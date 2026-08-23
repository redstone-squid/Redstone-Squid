"""Mounts following the shared cells their renders read."""

import asyncio
import gc
import weakref
from dataclasses import dataclass
from typing import Any

import anyio
import discord
import pytest

import squid_layouts as sl
from squid_layouts import Component, PressEvent, Shared, TopicBus, cell, state, transaction
from squid_layouts.discord import Everyone, Mount, Reactor
from squid_layouts.discord.testing import delivered_to, fake_interaction, fake_message
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


class Writer(Component):
    """A panel that writes the cell it renders, which is the case the bus alone handles badly."""

    def __init__(self, workspace: Workspace, *, feedback: sl.Feedback | None = None, run=None) -> None:
        self.workspace = workspace
        self.run = run
        self.feedback = feedback

    def render(self):
        return [
            Text(str(self.workspace.selected)),
            Row(
                (
                    Button(label="pick", on_click=self.pick, key="pick", feedback=self.feedback),
                    Button(label="aside", on_click=self.aside, key="aside"),
                    Button(label="boom", on_click=self.boom, key="boom"),
                )
            ),
        ]

    async def pick(self, event: PressEvent) -> None:
        if self.run is not None:
            await self.run()
        self.workspace.selected = 7

    async def aside(self, event: PressEvent) -> None:
        self.workspace.detail = "unrendered"

    async def boom(self, event: PressEvent) -> None:
        self.workspace.selected = 7
        message = "handler failed"
        raise RuntimeError(message)


class Swapper(Component):
    """A panel that reads one cell or the other, never both.

    `Panel` only ever adds a read, so it cannot express the case where a staged render stops
    depending on a cell the generation on screen still displays.
    """

    other: bool = state(default=False)

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def render(self):
        text = self.workspace.detail if self.other else str(self.workspace.selected)
        return [
            Text(text),
            Row(
                (
                    Button(label="pick", on_click=self.pick, key="pick"),
                    Button(label="detail", on_click=self.write_detail, key="detail"),
                )
            ),
        ]

    async def pick(self, event: PressEvent) -> None:
        self.workspace.selected = 7

    async def write_detail(self, event: PressEvent) -> None:
        self.workspace.detail = "new detail"


def _texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


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


async def test_a_discarded_staged_render_keeps_the_visible_generations_follow() -> None:
    """The unsafe direction: a render that stopped reading a cell may not unfollow it early.

    The message still shows `selected`, so dropping that subscription while the candidate is
    only staged leaves the panel deaf to every later write -- the bus is not durable, so
    nothing replays it once a successful render subscribes again.
    """
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    panel = Swapper(workspace)
    mount = Mount(panel, access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))
    assert mount.followed == (address(workspace, "selected"),)

    panel.other = True
    candidate = mount._stage()
    mount._rollback(candidate)
    assert address(workspace, "selected") in mount.followed

    refreshes = 0

    async def refresh_now(*, links=()) -> None:
        nonlocal refreshes
        refreshes += 1

    mount.refresh_now = refresh_now  # pyrefly: ignore
    with transaction():
        workspace.selected = 3
    await drain(reactor, bus)
    assert refreshes == 1


async def test_a_delivered_render_retires_what_the_old_one_needed() -> None:
    """The other half: pruning happens, just at the commit rather than at the stage."""
    bus = TopicBus()
    reactor = Reactor(bus)
    workspace = Workspace(bus, Member(1))
    panel = Swapper(workspace)
    mount = Mount(panel, access=Everyone(), scheduler=reactor)
    await mount.send(delivered_to(fake_message()))

    panel.other = True
    await mount.refresh_now()
    assert mount.followed == (address(workspace, "detail"),)
    assert {topic.topic for topic in bus.snapshot().topics} == {address(workspace, "detail")}


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


class TestSelfWrites:
    """A mount that writes a cell it renders repaints in the click, not one edit later."""

    def panel(self, *, feedback: sl.Feedback | None = None, run=None) -> tuple[Workspace, Writer, Mount]:
        bus = TopicBus()
        workspace = Workspace(bus, Member(1))
        panel = Writer(workspace, feedback=feedback, run=run)
        return workspace, panel, Mount(panel, access=Everyone(), timeout=None, pending_after=30)

    async def test_the_writing_mount_repaints_in_its_own_interaction(self) -> None:
        workspace, _, mount = self.panel()
        await mount.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await mount.dispatch("pick", interaction)

        assert workspace.selected == 7
        assert interaction.response.edit_message.await_count == 1, "the click edits, it does not defer"
        assert interaction.response.defer.await_count == 0
        assert "7" in _texts(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_it_works_without_a_reactor_to_deliver_the_topic(self) -> None:
        """The observed set is what the render read; subscribing is a separate, optional thing."""
        _, _, mount = self.panel()
        await mount.send(delivered_to(fake_message()))
        assert mount.followed == (), "no scheduler, so nothing is subscribed"
        assert mount.observed != ()

        interaction = fake_interaction()
        await mount.dispatch("pick", interaction)
        assert interaction.response.edit_message.await_count == 1

    async def test_a_write_to_a_cell_it_does_not_render_changes_nothing(self) -> None:
        _, panel, mount = self.panel()
        await mount.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await mount.dispatch("aside", interaction)

        assert interaction.response.edit_message.await_count == 0, "nothing it shows moved"
        assert interaction.response.defer.await_count == 1

    async def test_a_feedback_action_does_not_flash_the_stale_scene(self) -> None:
        """The bug this fixed: flush found nothing, so `restore` repainted the committed plan."""
        release = asyncio.Event()
        workspace, _, mount = self.panel(feedback=sl.Feedback(pending="Working…"), run=release.wait)
        mount.pending_after = 0
        await mount.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        async def press() -> None:
            await mount.dispatch("pick", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(press)
            while not interaction.response.edit_message.await_count:
                await asyncio.sleep(0)
            release.set()

        assert workspace.selected == 7
        final = interaction.followup.edit_message.await_args.kwargs["view"]
        assert "7" in _texts(final), "the last paint is the new scene, not the restored old one"

    async def test_a_rolled_back_action_leaves_the_mount_clean(self) -> None:
        _, _, mount = self.panel()
        await mount.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await mount.dispatch("boom", interaction)

        assert not mount.pending, "the commit hook never ran, so nothing marked it dirty"

    async def test_a_write_racing_a_candidate_survives_its_commit(self) -> None:
        workspace, _, mount = self.panel()
        message: Any = fake_message()
        await mount.send(delivered_to(message))
        started = asyncio.Event()
        release = asyncio.Event()

        async def edit(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await release.wait()
            return message

        message.edit = edit
        interaction = fake_interaction()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(mount.refresh_now)
            await started.wait()
            tasks.start_soon(mount.dispatch, "pick", interaction)
            while workspace.selected != 7:
                await asyncio.sleep(0)
            release.set()

        assert interaction.response.edit_message.await_count == 1
        assert "7" in _texts(interaction.response.edit_message.await_args.kwargs["view"])
        assert not mount.pending

    async def test_a_write_to_an_in_flight_candidates_new_read_keeps_it_dirty(self) -> None:
        bus = TopicBus()
        workspace = Workspace(bus, Member(1))
        panel = Swapper(workspace)
        mount = Mount(panel, access=Everyone(), timeout=None, pending_after=30)
        message: Any = fake_message()
        await mount.send(delivered_to(message))
        assert mount.observed == (address(workspace, "selected"),)

        started = asyncio.Event()
        release = asyncio.Event()

        async def edit(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await release.wait()
            return message

        message.edit = edit
        panel.other = True
        interaction = fake_interaction()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(mount.refresh_now)
            await started.wait()
            assert address(workspace, "detail") in mount._watched
            tasks.start_soon(mount.dispatch, "detail", interaction)
            while workspace.detail != "new detail":
                await asyncio.sleep(0)
            release.set()

        assert interaction.response.edit_message.await_count == 1
        assert "new detail" in _texts(interaction.response.edit_message.await_args.kwargs["view"])
        assert not mount.pending
