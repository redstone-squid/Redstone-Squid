"""Mounts following the shared cells their renders read."""

import asyncio
import gc
import weakref
from dataclasses import dataclass
from typing import Any

import anyio
import discord
import pytest

import squid_ui as sl
from squid_ui import Component, PressEvent, state
from squid_ui.primitives import Button, Row, Text
from squid_ui.profiling import PresentationStatus
from squid_ui.runtime import CellAddress, LocalTopicBus, SharedState, transaction
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int


class Workspace(SharedState[Member]):
    selected: int | None = state(None)
    detail: str = state("")


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

    def __init__(self, workspace: Workspace, *, busy: sl.interactions.BusySpec | None = None, run=None) -> None:
        self.workspace = workspace
        self.run = run
        self.busy = busy

    def render(self):
        return [
            Text(str(self.workspace.selected)),
            Row(
                (
                    Button(label="pick", on_click=self.pick, key="pick", busy=self.busy),
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


def address(workspace: Workspace, name: str) -> CellAddress:
    return CellAddress(workspace, name)


async def drain(scheduler: MessageRootScheduler, bus: LocalTopicBus) -> None:
    del bus
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await asyncio.wait_for(scheduler._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


async def test_two_mounts_react_once_each_to_one_commit() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    message_roots = [MessageRoot(Panel(workspace), access=Everyone(), scheduler=scheduler) for _ in range(2)]
    refreshes: dict[str, int] = {}
    for message_root in message_roots:
        await message_root.send(delivered_to(fake_message()))
        assert message_root.followed == (address(workspace, "selected"),)

        def counted(message_root: MessageRoot = message_root):
            async def refresh(*, links=()) -> None:
                refreshes[message_root.id] = refreshes.get(message_root.id, 0) + 1

            return refresh

        message_root.refresh = counted()  # pyrefly: ignore

    with transaction():
        workspace.selected = 3
        workspace.selected = 4
    await drain(scheduler, bus)

    assert refreshes == {message_roots[0].id: 1, message_roots[1].id: 1}


async def test_backdated_scheduled_refresh_skips_render_planning_and_drawing(monkeypatch) -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))

    class Parity(Component):
        def __init__(self) -> None:
            self.renders = 0

        @sl.computed
        def even(self) -> bool:
            return (workspace.selected or 0) % 2 == 0

        def render(self) -> Text:
            self.renders += 1
            return Text(str(self.even))

    component = Parity()
    message_root = MessageRoot(component, access=Everyone(), scheduler=scheduler, timeout=None)
    message: Any = fake_message()
    await message_root.send(delivered_to(message))
    issued = message_root._issued

    def unexpected(*_args, **_kwargs):
        message = "a backdated scheduled refresh must reuse the committed owner plan"
        raise AssertionError(message)

    monkeypatch.setattr(message_root, "_plan_tree", unexpected)
    monkeypatch.setattr(message_root, "_renderer", unexpected)

    with transaction():
        workspace.selected = 2
    await drain(scheduler, bus)

    assert component.renders == 1
    assert message_root._issued == issued
    assert message.edit.await_count == 0
    assert scheduler.snapshot().unchanged == 1


async def test_explicit_scheduler_request_resamples_opaque_component_inputs() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)

    class Opaque(Component):
        def __init__(self) -> None:
            self.value = "first"
            self.renders = 0

        def render(self) -> Text:
            self.renders += 1
            return Text(self.value)

    component = Opaque()
    message_root = MessageRoot(component, access=Everyone(), scheduler=scheduler, timeout=None)
    message: Any = fake_message()
    await message_root.send(delivered_to(message))
    component.value = "second"

    scheduler.schedule(message_root)
    await drain(scheduler, bus)

    assert component.renders == 2
    assert "second" in str(message.edit.await_args.kwargs["view"].to_components())


async def test_a_dropped_conditional_read_stops_refreshing() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    panel = Panel(workspace)
    panel.show_detail = True
    message_root = MessageRoot(panel, access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))
    assert set(message_root.followed) == {address(workspace, "selected"), address(workspace, "detail")}

    panel.show_detail = False
    await message_root.refresh()
    assert message_root.followed == (address(workspace, "selected"),)
    assert {topic.topic for topic in bus.snapshot().topics} == {address(workspace, "selected")}


async def test_a_discarded_staged_render_leaves_no_permanent_follow() -> None:
    """Over-subscribe, never under-subscribe: a failed delivery's follow is dropped next render."""
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    panel = Panel(workspace)
    message_root = MessageRoot(panel, access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))

    panel.show_detail = True
    candidate = message_root._stage()
    assert set(message_root.followed) == {address(workspace, "selected"), address(workspace, "detail")}
    message_root._rollback(candidate)

    panel.show_detail = False
    await message_root.refresh()
    assert message_root.followed == (address(workspace, "selected"),)


async def test_a_discarded_staged_render_keeps_the_visible_generations_follow() -> None:
    """The unsafe direction: a render that stopped reading a cell may not unfollow it early.

    The message still shows `selected`, so dropping that subscription while the candidate is
    only staged leaves the panel deaf to every later write -- the bus is not durable, so
    nothing replays it once a successful render subscribes again.
    """
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    panel = Swapper(workspace)
    message_root = MessageRoot(panel, access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))
    assert message_root.followed == (address(workspace, "selected"),)

    panel.other = True
    candidate = message_root._stage()
    message_root._rollback(candidate)
    assert address(workspace, "selected") in message_root.followed

    refreshes = 0

    async def refresh(*, links=()) -> None:
        nonlocal refreshes
        refreshes += 1

    message_root.refresh = refresh  # pyrefly: ignore
    with transaction():
        workspace.selected = 3
    await drain(scheduler, bus)
    assert refreshes == 1


async def test_a_delivered_render_retires_what_the_old_one_needed() -> None:
    """The other half: pruning happens, just at the commit rather than at the stage."""
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    panel = Swapper(workspace)
    message_root = MessageRoot(panel, access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))

    panel.other = True
    await message_root.refresh()
    assert message_root.followed == (address(workspace, "detail"),)
    assert {topic.topic for topic in bus.snapshot().topics} == {address(workspace, "detail")}


async def test_no_follow_outlives_its_root() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    message_root = MessageRoot(Panel(workspace), access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))
    assert bus.snapshot().topics != ()

    await message_root.finish(disable=False)
    assert message_root.followed == ()
    assert bus.snapshot().topics == ()


async def test_a_namespace_dropped_by_its_last_message_root_is_collected() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    workspace = Workspace(bus, Member(1))
    gone = weakref.ref(workspace)
    message_root = MessageRoot(Panel(workspace), access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message()))
    await message_root.finish(disable=False)

    del workspace, message_root
    gc.collect()
    assert gone() is None


async def test_a_scheduler_that_cannot_follow_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    bus = LocalTopicBus()
    workspace = Workspace(bus, Member(1))
    message_root = MessageRoot(Panel(workspace), access=Everyone())
    with caplog.at_level("WARNING"):
        await message_root.send(delivered_to(fake_message()))
        await message_root.refresh()
    assert sum("scheduler has no topic bus" in record.message for record in caplog.records) == 1


class TestSelfWrites:
    """A mount that writes a cell it renders repaints in the click, not one edit later."""

    def panel(self, *, busy: sl.interactions.BusySpec | None = None, run=None) -> tuple[Workspace, Writer, MessageRoot]:
        bus = LocalTopicBus()
        workspace = Workspace(bus, Member(1))
        panel = Writer(workspace, busy=busy, run=run)
        return workspace, panel, MessageRoot(panel, access=Everyone(), timeout=None, pending_after=30)

    async def test_the_writing_message_root_repaints_in_its_own_interaction(self) -> None:
        workspace, _, message_root = self.panel()
        await message_root.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await message_root.dispatch("pick", interaction)

        assert workspace.selected == 7
        assert interaction.response.edit_message.await_count == 1, "the click edits, it does not defer"
        assert interaction.response.defer.await_count == 0
        assert "7" in _texts(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_the_reactor_suppresses_the_self_published_render(self) -> None:
        bus = LocalTopicBus()
        scheduler = MessageRootScheduler(bus)
        workspace = Workspace(bus, Member(1))
        message_root = MessageRoot(Writer(workspace), access=Everyone(), scheduler=scheduler, timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))
        interaction = fake_interaction()

        await message_root.dispatch("pick", interaction)
        await drain(scheduler, bus)

        interaction.response.edit_message.assert_awaited_once()
        message.edit.assert_not_awaited()
        assert scheduler.snapshot().unchanged == 1
        assert message_root.snapshot().suppressed == 1

    async def test_a_suppressed_refresh_keeps_the_live_generation_dispatchable(self) -> None:
        workspace, _, message_root = self.panel()
        await message_root.send(delivered_to(fake_message()))
        generation = message_root.generation

        status = await message_root.refresh()
        interaction = fake_interaction()
        await message_root.dispatch("aside", interaction, generation=generation)

        assert status is PresentationStatus.UNCHANGED
        assert message_root.generation == generation
        assert workspace.detail == "unrendered"

    async def test_it_works_without_a_reactor_to_deliver_the_topic(self) -> None:
        """The observed set is what the render read; subscribing is a separate, optional thing."""
        _, _, message_root = self.panel()
        await message_root.send(delivered_to(fake_message()))
        assert message_root.followed == (), "no scheduler, so nothing is subscribed"
        assert message_root.observed != ()

        interaction = fake_interaction()
        await message_root.dispatch("pick", interaction)
        assert interaction.response.edit_message.await_count == 1

    async def test_a_write_to_a_cell_it_does_not_render_changes_nothing(self) -> None:
        _, panel, message_root = self.panel()
        await message_root.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await message_root.dispatch("aside", interaction)

        assert interaction.response.edit_message.await_count == 0, "nothing it shows moved"
        assert interaction.response.defer.await_count == 1

    async def test_a_feedback_action_does_not_flash_the_stale_scene(self) -> None:
        """The bug this fixed: flush found nothing, so `restore` repainted the committed plan."""
        release = asyncio.Event()
        workspace, _, message_root = self.panel(busy=sl.interactions.BusySpec(pending="Working…"), run=release.wait)
        message_root.pending_after = 0
        await message_root.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        async def press() -> None:
            await message_root.dispatch("pick", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(press)
            while not interaction.response.edit_message.await_count:
                await asyncio.sleep(0)
            release.set()

        assert workspace.selected == 7
        final = interaction.followup.edit_message.await_args.kwargs["view"]
        assert "7" in _texts(final), "the last paint is the new scene, not the restored old one"

    async def test_a_rolled_back_action_leaves_the_message_root_clean(self) -> None:
        _, _, message_root = self.panel()
        await message_root.send(delivered_to(fake_message()))
        interaction = fake_interaction()

        await message_root.dispatch("boom", interaction)

        assert not message_root.pending, "the commit hook never ran, so nothing marked it dirty"

    async def test_a_write_racing_a_candidate_survives_its_commit(self) -> None:
        workspace, _, message_root = self.panel()
        message: Any = fake_message()
        await message_root.send(delivered_to(message))
        started = asyncio.Event()
        release = asyncio.Event()

        async def edit(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await release.wait()
            return message

        message.edit = edit
        workspace.selected = 3
        interaction = fake_interaction()

        async def dispatch() -> None:
            await message_root.dispatch("pick", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.refresh)
            await started.wait()
            tasks.start_soon(dispatch)
            while workspace.selected != 7:
                await asyncio.sleep(0)
            release.set()

        assert interaction.response.edit_message.await_count == 1
        assert "7" in _texts(interaction.response.edit_message.await_args.kwargs["view"])
        assert not message_root.pending

    async def test_a_write_to_an_in_flight_candidates_new_read_keeps_it_dirty(self) -> None:
        bus = LocalTopicBus()
        workspace = Workspace(bus, Member(1))
        panel = Swapper(workspace)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=30)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))
        assert message_root.observed == (address(workspace, "selected"),)

        started = asyncio.Event()
        release = asyncio.Event()

        async def edit(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await release.wait()
            return message

        message.edit = edit
        panel.other = True
        interaction = fake_interaction()

        async def dispatch() -> None:
            await message_root.dispatch("detail", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.refresh)
            await started.wait()
            assert address(workspace, "detail") in message_root._subscriptions.watched
            tasks.start_soon(dispatch)
            while workspace.detail != "new detail":
                await asyncio.sleep(0)
            release.set()

        assert interaction.response.edit_message.await_count == 1
        assert "new detail" in _texts(interaction.response.edit_message.await_args.kwargs["view"])
        assert not message_root.pending
