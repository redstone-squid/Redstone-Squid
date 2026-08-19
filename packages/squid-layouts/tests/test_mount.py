"""Reactive core tests: state, dispatch funnel, flush, lifecycle."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anyio
import discord
import pytest

from squid_layouts import (
    ActionPolicy,
    Button,
    Component,
    Document,
    Mount,
    Option,
    PressEvent,
    ReactiveWriteError,
    Reactor,
    Row,
    SelectionEvent,
    SelectMenu,
    Text,
    assert_within_limits,
    batch,
    computed,
    state,
    transaction,
)
from squid_layouts.primitives import ActionGroup, Heading
from squid_layouts.testing import fake_interaction


class Counter(Component):
    count: int = state(0)

    def render(self):
        return [
            Heading("Counter"),
            Text(f"count: {self.count}"),
            Row((Button(label="+1", on_click=self.increment, key="inc"),)),
        ]

    async def increment(self, event: PressEvent) -> None:
        self.count += 1


class RootToolbar(Component):
    def render(self):
        return Document(
            (ActionGroup(tuple(Button(str(index), self.click, f"b{index}") for index in range(41))),),
            key="toolbar",
        )

    async def click(self, event: PressEvent) -> None: ...


def _button(view: discord.ui.LayoutView) -> discord.ui.Button:
    return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))


class TestRenderAndWire:
    def test_build_view_wires_handlers(self):
        mount = Mount(Counter(), timeout=None)
        view = mount.build_view()
        button = _button(view)
        assert button.custom_id is not None and button.custom_id.startswith(f"ctl:{mount.id}:1:inc")
        assert "inc" in mount._handlers
        assert_within_limits(view)

    def test_render_generations_have_distinct_control_ids(self):
        mount = Mount(Counter(), timeout=None)

        first = _button(mount.build_view())
        second = _button(mount.build_view())

        assert first.custom_id != second.custom_id

    async def test_keyed_document_root_pages_are_live_mount_navigation(self):
        mount = Mount(RootToolbar(), timeout=None)
        mount.build_view()

        assert mount.presentation.cursor("toolbar").extent > 1
        await mount.dispatch("__page_next.toolbar", fake_interaction())
        assert mount.presentation.cursor("toolbar").index == 1

    async def test_click_mutates_state_and_edits(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("inc", interaction)

        assert component.count == 1
        edited_view = interaction.response.edit_message.await_args.kwargs["view"]
        texts = [i.content for i in edited_view.walk_children() if isinstance(i, discord.ui.TextDisplay)]
        assert "count: 1" in texts

    async def test_press_event_carries_portable_actor_and_frontend_context(self):
        seen: list[PressEvent] = []

        class Inspect(Component):
            def render(self):
                return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

            async def inspect(self, event: PressEvent) -> None:
                seen.append(event)

        mount = Mount(Inspect(), timeout=None)
        mount.build_view()

        await mount.dispatch("inspect", fake_interaction(user_id=42))

        assert seen[0].actor.id == "42"
        assert seen[0].context == {"frontend": "discord"}

    async def test_clean_dispatch_defers_instead_of_editing(self):
        class Static(Counter):
            async def increment(self, event: PressEvent) -> None:
                pass  # no state change

        mount = Mount(Static(), timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("inc", interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()

    async def test_stale_key_is_acknowledged_not_crashed(self):
        mount = Mount(Counter(), timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("gone", interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_slow_handler_is_acknowledged_by_the_runtime_watchdog(self):
        started = anyio.Event()
        release = anyio.Event()

        class Slow(Component):
            def render(self):
                return Row((Button("slow", self.slow, "slow"),))

            async def slow(self, event: PressEvent) -> None:
                started.set()
                await release.wait()

        mount = Mount(Slow(), timeout=None, acknowledgement_timeout=0.01)
        mount.build_view()
        interaction = fake_interaction()

        async def dispatch() -> None:
            await mount.dispatch("slow", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch)
            await started.wait()
            await anyio.sleep(0.02)
            interaction.response.defer.assert_awaited_once()
            interaction.response._done = True
            release.set()


class TestAuthorLock:
    async def test_wrong_user_is_rejected_ephemerally(self):
        component = Counter()
        mount = Mount(component, timeout=None, lock_to=42)
        mount.build_view()
        interaction = fake_interaction(user_id=99)

        await mount.dispatch("inc", interaction)

        assert component.count == 0
        send = interaction.response.send_message
        assert send.await_args.kwargs["ephemeral"] is True

    async def test_owner_passes(self):
        component = Counter()
        mount = Mount(component, timeout=None, lock_to=42)
        mount.build_view()

        await mount.dispatch("inc", fake_interaction(user_id=42))

        assert component.count == 1


class TestActionPolicy:
    async def test_exclusive_action_from_a_stale_view_is_acknowledged_without_running(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.build_view()
        stale_generation = mount._generation
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("inc", interaction, generation=stale_generation)

        assert component.count == 0
        interaction.response.defer.assert_awaited_once()

    async def test_rebase_action_uses_the_handler_from_the_current_generation(self):
        calls: list[str] = []

        class Rebased(Component):
            current = False

            def render(self):
                handler = self.new if self.current else self.old
                return Row((Button("run", handler, "run", policy=ActionPolicy.REBASE),))

            async def old(self, event: PressEvent) -> None:
                calls.append("old")

            async def new(self, event: PressEvent) -> None:
                calls.append("new")

        component = Rebased()
        mount = Mount(component, timeout=None)
        mount.build_view()
        stale_generation = mount._generation
        component.current = True
        mount.build_view()

        await mount.dispatch("run", fake_interaction(), generation=stale_generation)

        assert calls == ["new"]

    async def test_exclusive_actions_do_not_overlap(self):
        active = 0
        maximum = 0

        class Serialized(Component):
            def render(self):
                return Row((Button("run", self.run, "run"),))

            async def run(self, event: PressEvent) -> None:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                await anyio.sleep(0)
                active -= 1

        mount = Mount(Serialized(), timeout=None)
        mount.build_view()

        async def dispatch(interaction) -> None:
            await mount.dispatch("run", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch, fake_interaction())
            tasks.start_soon(dispatch, fake_interaction())

        assert maximum == 1

    async def test_parallel_read_rolls_back_and_reports_state_writes(self):
        class Reader(Component):
            count: int = state(0)

            def render(self):
                return Row((Button("read", self.read, "read", policy=ActionPolicy.PARALLEL_READ),))

            async def read(self, event: PressEvent) -> None:
                self.count += 1

        component = Reader()
        hook = AsyncMock()
        mount = Mount(component, timeout=None, on_error=hook)
        mount.build_view()

        await mount.dispatch("read", fake_interaction())

        assert component.count == 0
        assert hook.await_args is not None
        assert isinstance(hook.await_args.args[1], ReactiveWriteError)


class TestErrors:
    async def test_handler_error_goes_to_hook(self):
        class Boom(Component):
            def render(self):
                return [Row((Button(label="x", on_click=self.explode, key="x"),))]

            async def explode(self, interaction) -> None:
                message = "boom"
                raise RuntimeError(message)

        hook = AsyncMock()
        mount = Mount(Boom(), timeout=None, on_error=hook)
        mount.build_view()

        await mount.dispatch("x", fake_interaction())

        assert hook.await_args is not None
        (_interaction, error, source), _ = hook.await_args
        assert isinstance(error, RuntimeError)
        assert source == "handler:x"

    async def test_failed_handler_rolls_back_all_state_changes(self):
        class Boom(Component):
            count: int = state(0)
            entries: list[str] = state(factory=list)

            def render(self):
                return [Row((Button(label="x", on_click=self.explode, key="x"),))]

            async def explode(self, interaction) -> None:
                self.count = 1
                self.entries.append("partial")
                message = "boom"
                raise RuntimeError(message)

        component = Boom()
        hook = AsyncMock()
        mount = Mount(component, timeout=None, on_error=hook)
        mount.build_view()

        await mount.dispatch("x", fake_interaction())

        assert component.count == 0
        assert component.entries == []
        assert not mount._dirty


class TestSelect:
    async def test_select_handler_receives_values(self):
        picked: list[str] = []

        class Picker(Component):
            def render(self):
                return [
                    SelectMenu(
                        options=(Option("A", "a"), Option("B", "b")),
                        on_select=self.pick,
                        key="pick",
                    )
                ]

            async def pick(self, event: SelectionEvent) -> None:
                picked.extend(event.values)

        mount = Mount(Picker(), timeout=None)
        view = mount.build_view()
        assert any(isinstance(item, discord.ui.Select) for item in view.walk_children())

        await mount.dispatch("pick", fake_interaction(), ["b"])

        assert picked == ["b"]


class TestLifecycle:
    async def test_finish_disables_controls(self):
        mount = Mount(Counter(), timeout=None)
        view = mount.build_view()
        message: Any = SimpleNamespace(
            flags=SimpleNamespace(components_v2=True),
            edit=AsyncMock(return_value=SimpleNamespace(flags=SimpleNamespace(components_v2=True))),
        )
        mount.bind(message, view)

        await mount.finish()

        disabled_view = message.edit.await_args.kwargs["view"]
        assert _button(disabled_view).disabled
        interaction = fake_interaction()
        await mount.dispatch("inc", interaction)  # finished mounts ignore late clicks
        interaction.response.edit_message.assert_not_awaited()

    async def test_refresh_now_edits_bound_message(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        view = mount.build_view()
        message: Any = SimpleNamespace(
            flags=SimpleNamespace(components_v2=True),
            edit=AsyncMock(return_value=SimpleNamespace(flags=SimpleNamespace(components_v2=True))),
        )
        mount.bind(message, view)
        component.count = 7

        await mount.refresh_now()

        message.edit.assert_awaited_once()

    async def test_reactor_coalesces_double_schedule(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.refresh_now = AsyncMock()  # pyrefly: ignore
        reactor = Reactor()
        reactor.schedule(mount)
        reactor.schedule(mount)
        assert reactor._queue.qsize() == 1


class TestStateDescriptor:
    def test_default_is_per_instance(self):
        first, second = Counter(), Counter()
        first.count = 5
        assert second.count == 0

    def test_assignment_marks_mount_dirty(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.build_view()
        assert not mount._dirty
        component.count = 3
        assert mount._dirty

    def test_mutable_factory_is_per_instance_and_observed(self):
        class Collection(Component):
            entries: list[dict[str, int]] = state(factory=list)

            def render(self):
                return Text(str(self.entries))

        first, second = Collection(), Collection()
        mount = Mount(first, timeout=None)
        mount.build_view()

        first.entries.append({"count": 1})

        assert mount._dirty
        assert second.entries == []

        mount.build_view()
        first.entries[0]["count"] = 2
        assert mount._dirty

    def test_computed_values_cache_until_state_changes(self):
        class Derived(Component):
            count: int = state(1)

            def __init__(self) -> None:
                self.calls = 0

            @computed
            def doubled(self) -> int:
                self.calls += 1
                return self.count * 2

            def render(self):
                return Text(str(self.doubled))

        component = Derived()
        assert component.doubled == 2
        assert component.doubled == 2
        assert component.calls == 1

        component.count = 3
        assert component.doubled == 6
        assert component.calls == 2

    def test_batch_coalesces_invalidations(self):
        class Pair(Component):
            left: int = state(0)
            right: int = state(0)

            def __init__(self) -> None:
                self.invalidations = 0

            def invalidate(self) -> None:
                self.invalidations += 1
                super().invalidate()

            def render(self):
                return Text(f"{self.left}:{self.right}")

        component = Pair()
        with batch():
            component.left = 1
            component.right = 2

        assert component.invalidations == 1

    def test_transaction_rolls_back_assignments_and_nested_mutation(self):
        class Form(Component):
            name: str = state("before")
            values: list[int] = state(factory=list)

            def render(self):
                return Text(self.name)

        component = Form()
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.name = "after"
            component.values.append(1)
            raise RuntimeError("abort")

        assert component.name == "before"
        assert component.values == []
