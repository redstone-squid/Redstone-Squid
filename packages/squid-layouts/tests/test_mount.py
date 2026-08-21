"""Reactive core tests: state, dispatch funnel, flush, lifecycle."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import discord
import pytest

from squid_layouts import (
    ActionPolicy,
    Component,
    Document,
    LayoutNode,
    PressEvent,
    ReactiveWriteError,
    SelectionEvent,
    batch,
    computed,
    state,
    transaction,
)
from squid_layouts.discord import (
    Mount,
    Reactor,
    delivery,
)
from squid_layouts.discord.testing import assert_within_limits, commit_render, fake_interaction, fake_message
from squid_layouts.primitives import (
    ActionGroup,
    Button,
    Heading,
    Lines,
    Option,
    Paginate,
    Row,
    SelectMenu,
    Text,
)


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


class Child(Component):
    def __init__(self, mounted: list[str]) -> None:
        self.mounted = mounted

    def render(self):
        return Text("child")

    def on_mount(self) -> None:
        self.mounted.append("child")


class Panel(Component):
    """A pager, a button and an optional child — one of each thing a commit publishes."""

    entries: list[str] = state(factory=lambda: [f"entry {index}" for index in range(6)])
    show_child: bool = state(default=False)

    def __init__(self, mounted: list[str]) -> None:
        self.child = Child(mounted)

    def render(self):
        nodes: list[LayoutNode] = [
            Lines(tuple(self.entries), overflow=Paginate(key="entries", per=2)),
            Row((Button("add", self.add, "add"),)),
        ]
        if self.show_child:
            nodes.append(self.embed(self.child, key="child"))
        return nodes

    async def add(self, event: PressEvent) -> None:
        self.entries.append("added")
        self.show_child = True


def _http_error() -> discord.HTTPException:
    response = cast(Any, SimpleNamespace(status=500, reason="Internal Server Error"))
    return discord.HTTPException(response, "edit refused")


async def _refuse_edit(*args: Any, **kwargs: Any) -> None:
    raise _http_error()


class _RefusingHandle:
    """An edit handle Discord rejects for a reason that is not staleness."""

    permanent = False
    expires_at = None

    def expired(self) -> bool:
        return False

    async def write(self, *args: Any, **kwargs: Any) -> None:
        raise _http_error()


def _refuse_handle(*args: Any, **kwargs: Any) -> _RefusingHandle:
    return _RefusingHandle()


def _button(view: discord.ui.LayoutView) -> discord.ui.Button:
    return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))


class TestRenderAndWire:
    def test_build_view_wires_handlers(self):
        mount = Mount(Counter(), timeout=None)
        view = commit_render(mount)
        button = _button(view)
        assert button.custom_id is not None and button.custom_id.startswith(f"ctl:{mount.id}:1:inc")
        assert "inc" in mount._handlers
        assert_within_limits(view)

    def test_render_generations_have_distinct_control_ids(self):
        mount = Mount(Counter(), timeout=None)

        first = _button(commit_render(mount))
        second = _button(commit_render(mount))

        assert first.custom_id != second.custom_id

    async def test_keyed_document_root_pages_are_live_mount_navigation(self):
        mount = Mount(RootToolbar(), timeout=None)
        commit_render(mount)

        assert mount.presentation.cursor("toolbar").extent > 1
        await mount.dispatch("__page_next.toolbar", fake_interaction())
        assert mount.presentation.cursor("toolbar").index == 1

    async def test_click_mutates_state_and_edits(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        commit_render(mount)
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
        commit_render(mount)

        await mount.dispatch("inspect", fake_interaction(user_id=42))

        assert seen[0].actor.id == "42"
        assert seen[0].context == {"frontend": "discord"}

    async def test_clean_dispatch_defers_instead_of_editing(self):
        class Static(Counter):
            async def increment(self, event: PressEvent) -> None:
                pass  # no state change

        mount = Mount(Static(), timeout=None)
        commit_render(mount)
        interaction = fake_interaction()

        await mount.dispatch("inc", interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()

    async def test_stale_key_is_acknowledged_not_crashed(self):
        mount = Mount(Counter(), timeout=None)
        commit_render(mount)
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
        commit_render(mount)
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
        commit_render(mount)
        interaction = fake_interaction(user_id=99)

        await mount.dispatch("inc", interaction)

        assert component.count == 0
        send = interaction.response.send_message
        assert send.await_args.kwargs["ephemeral"] is True

    async def test_owner_passes(self):
        component = Counter()
        mount = Mount(component, timeout=None, lock_to=42)
        commit_render(mount)

        await mount.dispatch("inc", fake_interaction(user_id=42))

        assert component.count == 1


class TestActionPolicy:
    async def test_exclusive_action_from_a_stale_view_is_acknowledged_without_running(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        commit_render(mount)
        stale_generation = mount._generation
        commit_render(mount)
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
        commit_render(mount)
        stale_generation = mount._generation
        component.current = True
        commit_render(mount)

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
        commit_render(mount)

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
        commit_render(mount)

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
        commit_render(mount)

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
        commit_render(mount)

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
        view = commit_render(mount)
        assert any(isinstance(item, discord.ui.Select) for item in view.walk_children())

        await mount.dispatch("pick", fake_interaction(), ["b"])

        assert picked == ["b"]


class TestLifecycle:
    async def test_finish_disables_controls(self):
        mount = Mount(Counter(), timeout=None)
        view = commit_render(mount)
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
        view = commit_render(mount)
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


class TestDeliveryAtomicity:
    """A render becomes the mount's state only once Discord has accepted it."""

    def test_build_view_stages_and_bind_commits(self):
        mount = Mount(Counter(), timeout=None)

        view = mount.build_view()

        assert mount._handlers == {}
        assert mount._generation == 0
        assert mount._assets == ()

        mount.bind(None, view)

        assert "inc" in mount._handlers
        assert mount._generation == 1

    async def test_failed_edit_keeps_the_visible_generation_live(self, monkeypatch):
        mounted: list[str] = []
        panel = Panel(mounted)
        mount = Mount(panel, timeout=None)
        commit_render(mount)
        await mount.dispatch("__page_next.entries", fake_interaction())
        assert mount.presentation.cursor("entries").index == 1

        live_generation = mount._generation
        live_handlers = mount._handlers
        live_strategies = dict(mount.presentation.strategies)
        panel.entries.append("entry 6")  # a new fingerprint: the staged render resets the cursor
        panel.show_child = True  # a component the failed generation must not mount

        monkeypatch.setattr(delivery, "handle_from", _refuse_handle)
        with pytest.raises(discord.HTTPException):
            await mount.flush(fake_interaction())

        assert mount._generation == live_generation
        assert mount._handlers is live_handlers
        assert mount._dirty
        assert mounted == []
        assert mount.presentation.cursor("entries").index == 1
        # Planning only reads the session, so a discarded candidate leaves behind none of
        # its writes — not just the cursors the old snapshot happened to restore.
        assert mount.presentation.strategies == live_strategies

    async def test_a_click_after_a_failed_edit_still_runs_and_repairs_the_message(self, monkeypatch):
        mounted: list[str] = []
        panel = Panel(mounted)
        mount = Mount(panel, timeout=None)
        commit_render(mount)
        live_generation = mount._generation
        panel.show_child = True

        monkeypatch.setattr(delivery, "handle_from", _refuse_handle)
        with pytest.raises(discord.HTTPException):
            await mount.flush(fake_interaction())
        monkeypatch.undo()

        # The stale-generation guard would silently defer this click if the mount had
        # advanced past the generation the message is still showing.
        interaction = fake_interaction()
        await mount.dispatch("add", interaction, generation=live_generation)

        assert panel.entries[-1] == "added"
        assert mount._generation > live_generation
        assert not mount._dirty
        assert mounted == ["child"]
        interaction.response.edit_message.assert_awaited_once()

    async def test_failed_refresh_leaves_the_mount_repairable(self, monkeypatch):
        component = Counter()
        mount = Mount(component, timeout=None)
        view = commit_render(mount)
        message: Any = SimpleNamespace(
            flags=SimpleNamespace(components_v2=True),
            edit=AsyncMock(side_effect=_http_error()),
        )
        mount.bind(message, view)
        component.count = 7
        live_generation = mount._generation

        with pytest.raises(discord.HTTPException):
            await mount.refresh_now()

        assert mount._generation == live_generation
        assert mount._dirty

        message.edit = AsyncMock(return_value=SimpleNamespace(flags=SimpleNamespace(components_v2=True)))
        await mount.refresh_now()

        assert mount._generation > live_generation
        assert not mount._dirty


class TestStateDescriptor:
    def test_default_is_per_instance(self):
        first, second = Counter(), Counter()
        first.count = 5
        assert second.count == 0

    def test_assignment_marks_mount_dirty(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        commit_render(mount)
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
        commit_render(mount)

        first.entries.append({"count": 1})

        assert mount._dirty
        assert second.entries == []

        commit_render(mount)
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


class Notifier(Component):
    """Writes state and then answers with a notice — the shape that broke the panel."""

    count: int = state(0)

    def render(self):
        return [Text(f"count: {self.count}"), Row((Button(label="go", on_click=self.go, key="go"),))]

    async def go(self, event: PressEvent) -> None:
        self.count += 1
        await event.notice("heads up")


class TestEditHandles:
    def test_handle_from_refuses_an_interaction_spent_on_another_message(self):
        spent = fake_interaction()
        spent.response.type = discord.InteractionResponseType.channel_message
        assert delivery.handle_from(spent) is None

        modal = fake_interaction()
        modal.response.type = discord.InteractionResponseType.modal
        assert delivery.handle_from(modal) is None

    def test_handle_from_accepts_an_unspent_or_update_shaped_interaction(self):
        assert delivery.handle_from(fake_interaction()) is not None

        deferred = fake_interaction()
        deferred.response.type = discord.InteractionResponseType.deferred_message_update
        handle = delivery.handle_from(deferred)
        assert handle is not None
        assert not handle.permanent
        assert handle.expires_at == deferred.expires_at

    def test_message_handle_is_permanent_only_off_the_channel(self):
        assert delivery.handle_for(fake_message()).permanent
        assert not delivery.handle_for(fake_message(ephemeral=True)).permanent

    async def test_a_notice_does_not_swallow_the_render_it_came_with(self):
        # `notice` answers with a new message, which moves the interaction's original
        # response off the panel. Editing through it would overwrite the notice and leave
        # the panel stale, so the mount falls back to the message it holds.
        message = fake_message()
        mount = Mount(Notifier(), timeout=None)
        mount.bind(message, mount.build_view())

        interaction = fake_interaction()
        await mount.dispatch("go", interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.edit_message.assert_not_awaited()
        message.edit.assert_awaited_once()
        assert not mount.pending

    async def test_a_click_renews_an_ephemeral_mount_for_background_refreshes(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.bind(fake_message(ephemeral=True), mount.build_view())
        assert mount.handle is not None and not mount.handle.permanent

        interaction = fake_interaction()
        await mount.dispatch("inc", interaction)
        assert mount.handle is not None
        assert mount.handle.expires_at == interaction.expires_at

        component.count += 1
        await mount.refresh_now()

        interaction.followup.edit_message.assert_awaited_once()
        assert interaction.followup.edit_message.await_args.args[0] == interaction.message.id
        assert not mount.pending

    async def test_a_click_does_not_trade_away_the_bots_own_credentials(self):
        message = fake_message()
        mount = Mount(Counter(), timeout=None)
        mount.bind(message, mount.build_view())
        permanent = mount.handle

        await mount.dispatch("inc", fake_interaction())

        assert mount.handle is permanent

    async def test_an_unreachable_mount_holds_its_render_for_the_next_click(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        mount.bind(fake_message(ephemeral=True), mount.build_view())
        mount._handle = delivery.handle_from(fake_interaction(expired=True))
        component.count += 1

        await mount.refresh_now()

        # Not an error and not the end of the mount: the message is simply out of reach
        # until someone clicks it again.
        assert mount.pending
        assert not mount._finished

        interaction = fake_interaction()
        await mount.dispatch("inc", interaction)

        interaction.response.edit_message.assert_awaited_once()
        assert not mount.pending
        assert component.count == 2

    async def test_a_stale_handle_is_dropped_rather_than_reused(self):
        class _Stale:
            permanent = False
            expires_at = None
            writes = 0

            def expired(self) -> bool:
                return False

            async def write(self, *args: Any, **kwargs: Any) -> None:
                type(self).writes += 1
                raise delivery.StaleHandleError("gone")

        component = Counter()
        mount = Mount(component, timeout=None)
        mount.bind(fake_message(ephemeral=True), mount.build_view())
        mount._handle = _Stale()
        component.count += 1

        await mount.refresh_now()
        await mount.refresh_now()

        assert _Stale.writes == 1
        assert mount.handle is None
        assert mount.pending
