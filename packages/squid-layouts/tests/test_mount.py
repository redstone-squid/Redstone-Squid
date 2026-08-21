"""Reactive core tests: state, dispatch funnel, flush, lifecycle."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import discord
import pytest

from squid_layouts import (
    ActionPolicy,
    Asset,
    Component,
    Document,
    InlineAsset,
    LayoutInvariantError,
    LayoutNode,
    Localization,
    Message,
    Paragraph,
    PressEvent,
    ReactiveWriteError,
    SelectionEvent,
    batch,
    computed,
    state,
    transaction,
)
from squid_layouts.chrome import LOCALIZATION_CONTEXT, Chrome
from squid_layouts.discord import (
    Mount,
    Reactor,
    delivery,
)
from squid_layouts.discord.mount import _custom_id
from squid_layouts.discord.testing import (
    assert_within_limits,
    commit_render,
    delivered_to,
    fake_interaction,
    fake_message,
)
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
from squid_layouts.runtime import ComponentRuntime
from squid_layouts.runtime.reactivity import _CURRENT


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

    def test_custom_id_digests_do_not_collide_across_a_shared_prefix(self):
        shared_prefix = "section." * 20
        first = _custom_id("mount", 1, shared_prefix + "one")
        second = _custom_id("mount", 1, shared_prefix + "two")

        assert len(first) <= 100
        assert len(second) <= 100
        assert first != second

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

    async def test_press_event_carries_the_mounts_negotiated_locale(self):
        seen: list[PressEvent] = []

        class Inspect(Component):
            def render(self):
                return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

            async def inspect(self, event: PressEvent) -> None:
                seen.append(event)

        mount = Mount(Inspect(), localization=Localization("zh-CN"), timeout=None)
        commit_render(mount)

        await mount.dispatch("inspect", fake_interaction())

        assert seen[0].locale == "zh-CN"

    def test_localize_retranslates_content_chrome_and_runtime_context(self):
        class Localized(Component):
            def render(self):
                return [
                    Paragraph(Message("Hello")),
                    Lines(("a", "b"), overflow=Paginate(key="lines", per=1)),
                ]

        translated = {"Hello": "Bonjour", "Previous": "Précédent", "Next": "Suivant"}
        chrome = Chrome(previous=Message("Previous"), next=Message("Next"))
        mount = Mount(Localized(), chrome=chrome, localization=Localization("en"), timeout=None)
        commit_render(mount)

        localization = Localization("fr", gettext=lambda message: translated.get(message, message))
        mount.localize(localization)
        view = commit_render(mount)

        texts = [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
        labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        assert "Bonjour" in texts
        assert labels == ["Précédent", "Suivant"]
        assert mount.runtime.context[LOCALIZATION_CONTEXT] is localization

    async def test_notice_resolves_deferred_text_with_mount_localization(self):
        class Notify(Component):
            def render(self):
                return Row((Button(label="notify", on_click=self.notify, key="notify"),))

            async def notify(self, event: PressEvent) -> None:
                await event.notice(Message("Notice"))

        localization = Localization("fr", gettext=lambda message: "Avis" if message == "Notice" else message)
        mount = Mount(Notify(), localization=localization, timeout=None)
        commit_render(mount)
        interaction = fake_interaction()

        await mount.dispatch("notify", interaction)

        interaction.response.send_message.assert_awaited_once()
        notice = interaction.response.send_message.await_args.kwargs["view"]
        assert [item.content for item in notice.walk_children() if isinstance(item, discord.ui.TextDisplay)] == ["Avis"]

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

    async def test_a_set_admits_every_member(self):
        component = Counter()
        mount = Mount(component, timeout=None, lock_to={42, 43})
        commit_render(mount)

        await mount.dispatch("inc", fake_interaction(user_id=42))
        await mount.dispatch("inc", fake_interaction(user_id=43))

        assert component.count == 2

    async def test_a_set_still_rejects_a_stranger(self):
        component = Counter()
        mount = Mount(component, timeout=None, lock_to={42, 43})
        commit_render(mount)

        await mount.dispatch("inc", fake_interaction(user_id=99))

        assert component.count == 0

    async def test_a_bare_id_normalizes_to_a_set(self):
        assert Mount(Counter(), timeout=None, lock_to=42).lock_to == frozenset({42})
        assert Mount(Counter(), timeout=None).lock_to is None


class TestFinishHooks:
    async def test_a_hook_fires_on_finish(self):
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))

        await mount.finish(disable=False)

        assert seen == [mount]

    async def test_a_hook_fires_on_finish_via(self):
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))
        commit_render(mount)

        await mount.finish_via(fake_interaction())

        assert seen == [mount]

    async def test_a_hook_fires_on_timeout(self):
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))

        await mount.handle_timeout()

        assert seen == [mount]

    async def test_a_hook_fires_once_across_repeated_finishes(self):
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))

        await mount.finish(disable=False)
        await mount.finish(disable=False)
        await mount.handle_timeout()

        assert seen == [mount]

    async def test_hooks_run_in_registration_order(self):
        mount = Mount(Counter(), timeout=None)
        order: list[str] = []
        mount.on_finish(lambda _: _note(order, "first"))
        mount.on_finish(lambda _: _note(order, "second"))

        await mount.finish(disable=False)

        assert order == ["first", "second"]

    async def test_a_raising_hook_does_not_stop_the_others_or_teardown(self):
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []

        async def explode(_: Mount) -> None:
            raise RuntimeError("observer is broken")

        mount.on_finish(explode)
        mount.on_finish(lambda finished: _record(seen, finished))
        commit_render(mount)

        await mount.finish(disable=False)

        assert seen == [mount]
        assert mount.finished
        assert mount._view is None

    async def test_a_hook_fires_even_when_the_disable_edit_raises(self):
        """The mount is finished and torn down either way, so an observer must hear about it.

        `finish_via` re-raises past its own `finally`, which is where the hooks have to run.
        """
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))
        commit_render(mount)
        interaction = fake_interaction()
        interaction.response.edit_message = AsyncMock(side_effect=RuntimeError("gateway is down"))

        with pytest.raises(RuntimeError):
            await mount.finish_via(interaction)

        assert seen == [mount]
        assert mount.finished

    async def test_a_hook_fires_even_when_finish_hits_an_unanticipated_error(self):
        """`finish` anticipates `HTTPException` from its disable-edit and nothing else.

        Anything it did not anticipate used to propagate past the teardown as well as the
        hooks, leaving the mount half-finished and every observer holding it.
        """
        mount = Mount(Counter(), timeout=None)
        seen: list[Mount] = []
        mount.on_finish(lambda finished: _record(seen, finished))
        message: Any = fake_message()
        message.edit = AsyncMock(side_effect=RuntimeError("message is gone"))
        await mount.send(delivered_to(message))

        with pytest.raises(RuntimeError):
            await mount.finish()

        assert seen == [mount]
        assert mount.finished
        assert mount._view is None

    async def test_finishing_from_inside_a_hook_does_not_recurse(self):
        mount = Mount(Counter(), timeout=None)
        calls: list[int] = []

        async def finish_again(finished: Mount) -> None:
            calls.append(1)
            await finished.finish(disable=False)

        mount.on_finish(finish_again)

        await mount.finish(disable=False)

        assert calls == [1]

    async def test_finished_flips_only_once_the_mount_is_done(self):
        mount = Mount(Counter(), timeout=None)

        assert not mount.finished

        await mount.finish(disable=False)

        assert mount.finished

    async def test_a_late_click_never_reaches_the_handler(self):
        """`view.stop()` hides this in production; a superseded-but-visible message does not."""
        component = Counter()
        mount = Mount(component, timeout=None)
        commit_render(mount)
        await mount.finish(disable=False)
        interaction = fake_interaction()

        await mount.dispatch("inc", interaction)

        assert component.count == 0
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def _record(seen: list[Mount], mount: Mount) -> None:
    seen.append(mount)


async def _note(order: list[str], label: str) -> None:
    order.append(label)


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
        message: Any = fake_message()
        await mount.send(delivered_to(message))

        await mount.finish()

        disabled_view = message.edit.await_args.kwargs["view"]
        assert _button(disabled_view).disabled
        interaction = fake_interaction()
        await mount.dispatch("inc", interaction)  # finished mounts ignore late clicks
        interaction.response.edit_message.assert_not_awaited()

    async def test_refresh_now_edits_bound_message(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        message: Any = fake_message()
        await mount.send(delivered_to(message))
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

    def test_build_view_stages_without_committing(self):
        """The stage-only escape hatch renders the tree and publishes none of it.

        Committing is `send`'s and `flush`'s job; `TestSend` covers the other half.
        """
        mount = Mount(Counter(), timeout=None)

        mount.build_view()

        assert mount._handlers == {}
        assert mount._generation == 0
        assert mount._assets == ()

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
        message: Any = fake_message()
        message.edit = AsyncMock(side_effect=_http_error())
        await mount.send(delivered_to(message))
        component.count = 7
        live_generation = mount._generation

        with pytest.raises(discord.HTTPException):
            await mount.refresh_now()

        assert mount._generation == live_generation
        assert mount._dirty

        message.edit = AsyncMock(return_value=message)
        await mount.refresh_now()

        assert mount._generation > live_generation
        assert not mount._dirty


class _Destination:
    """A recording destination. `message` is whatever it hands back to the mount."""

    def __init__(self, message: Any = None, *, raises: Exception | None = None) -> None:
        self.message = message
        self.raises = raises
        self.calls: list[tuple[discord.ui.LayoutView, list[discord.File]]] = []

    async def __call__(self, view: discord.ui.LayoutView, files: list[discord.File]) -> Any:
        self.calls.append((view, files))
        if self.raises is not None:
            raise self.raises
        return self.message


class Report(Component):
    """A component carrying one inline asset, so a send has files to hand over."""

    def render(self):
        return Document(
            (Text("summary"),),
            (Asset("report", "report.txt", "text/plain", InlineAsset(b"full report")),),
        )


class TestSend:
    """`Mount.send` runs stage -> deliver -> commit; the destination only says where."""

    async def test_a_successful_send_commits_and_keeps_the_message_handle(self):
        mount = Mount(Counter(), timeout=None)
        message = fake_message()
        destination = _Destination(message)

        sent = await mount.send(destination)

        assert sent is message
        assert "inc" in mount._handlers
        assert mount._generation == 1
        assert not mount.pending
        assert mount.handle is not None
        assert mount.handle.permanent

    async def test_a_destination_with_no_message_commits_and_waits_for_the_first_click(self):
        component = Counter()
        mount = Mount(component, timeout=None)

        sent = await mount.send(_Destination(None))

        # Delivered, so the render is live -- but nothing came back to write through.
        assert sent is None
        assert mount._generation == 1
        assert not mount.pending
        assert mount.handle is None

        # The first click renews the mount, exactly as an ephemeral send relies on.
        await mount.dispatch("inc", fake_interaction())

        assert component.count == 1
        assert mount.handle is not None

    async def test_an_abandoned_delivery_leaves_the_mount_resendable(self):
        mount = Mount(Counter(), timeout=None)
        abandoned = _Destination(raises=delivery.DeliveryAbandoned())

        sent = await mount.send(abandoned)

        # Nothing reached Discord, so nothing is live: no handlers, no handle, still dirty.
        assert sent is None
        assert mount._generation == 0
        assert mount._handlers == {}
        assert mount.handle is None
        assert mount.pending

        message = fake_message()
        assert await mount.send(_Destination(message)) is message
        # Generation 2, not 1: the abandoned candidate does not hand its control ids on.
        assert mount._generation == 2
        assert not mount.pending

    async def test_a_failed_delivery_propagates_and_the_next_send_recovers(self):
        mounted: list[str] = []
        panel = Panel(mounted)
        mount = Mount(panel, timeout=None)
        panel.show_child = True

        with pytest.raises(discord.HTTPException):
            await mount.send(_Destination(raises=_http_error()))

        assert mount._generation == 0
        assert mount._handlers == {}
        assert mount.pending
        # A candidate that was never delivered must not fire its lifecycle hooks.
        assert mounted == []

        await mount.send(_Destination(fake_message()))

        assert mount._generation > 0
        assert not mount.pending
        assert mounted == ["child"]

    async def test_the_staged_assets_reach_the_destination(self):
        mount = Mount(Report(), timeout=None)
        destination = _Destination(fake_message())

        await mount.send(destination)

        _, files = destination.calls[0]
        assert [file.filename for file in files] == ["report.txt"]

    async def test_send_supersedes_a_render_that_was_only_staged(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        staged = mount.build_view()
        component.count = 5

        destination = _Destination(fake_message())
        await mount.send(destination)

        delivered, _ = destination.calls[0]
        assert delivered is not staged
        assert staged.is_finished()
        assert mount._pending is None
        # The delivered generation is the one the mount is now live on.
        assert mount._view is delivered

    async def test_a_finished_mount_does_not_send(self):
        mount = Mount(Counter(), timeout=None)
        await mount.finish(disable=False)
        destination = _Destination(fake_message())

        assert await mount.send(destination) is None
        assert destination.calls == []


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
        await mount.send(delivered_to(message))

        interaction = fake_interaction()
        await mount.dispatch("go", interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.edit_message.assert_not_awaited()
        message.edit.assert_awaited_once()
        assert not mount.pending

    async def test_a_flush_through_the_standing_handle_still_answers_the_click(self):
        # A modal submitted from a command rather than from a component carries no message,
        # so `handle_from` has nothing to build on and the edit goes through the mount's own
        # handle. Only the interaction's handle answers the click by editing through it, so
        # the flush owes an acknowledgement -- without one Discord reports a failure at 3s.
        message = fake_message()
        component = Counter()
        mount = Mount(component, timeout=None)
        await mount.send(delivered_to(message))

        component.count = 3
        interaction = fake_interaction()
        interaction.message = None

        await mount.flush(interaction)

        message.edit.assert_awaited_once()
        interaction.response.defer.assert_awaited_once()
        assert not mount.pending

    async def test_a_click_renews_an_ephemeral_mount_for_background_refreshes(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        await mount.send(delivered_to(fake_message(ephemeral=True)))
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
        await mount.send(delivered_to(message))
        permanent = mount.handle

        await mount.dispatch("inc", fake_interaction())

        assert mount.handle is permanent

    async def test_an_unreachable_mount_holds_its_render_for_the_next_click(self):
        component = Counter()
        mount = Mount(component, timeout=None)
        await mount.send(delivered_to(fake_message(ephemeral=True)))
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
        await mount.send(delivered_to(fake_message(ephemeral=True)))
        mount._handle = _Stale()
        component.count += 1

        await mount.refresh_now()
        await mount.refresh_now()

        assert _Stale.writes == 1
        assert mount.handle is None
        assert mount.pending


class Leaf(Component):
    """A component that only knows what it renders once its `on_load` has run."""

    label: str = state("")

    def __init__(self, log: list[str], name: str = "leaf") -> None:
        self.log = log
        self.name = name

    async def on_load(self) -> None:
        self.log.append(f"load:{self.name}")
        self.label = f"{self.name} loaded"

    def render(self):
        self.log.append(f"render:{self.name}")
        return Text(self.label)


class Host(Component):
    """A loading parent whose child is only reachable through its loaded render."""

    ready: bool = state(default=False)

    def __init__(self, log: list[str], child: Component) -> None:
        self.log = log
        self.child = child

    async def on_load(self) -> None:
        self.log.append("load:host")
        self.ready = True

    def render(self):
        self.log.append("render:host")
        nodes: list[LayoutNode] = [Text("host")]
        if self.ready:
            nodes.append(self.embed(self.child, key="child"))
        return nodes


class TestDeferredExpansion:
    """A discovery render: what `on_load` needs to run before anything renders."""

    def test_expansion_stops_at_a_deferred_child(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)

        tree = runtime.render(defer=lambda component: component is child)

        assert tree.deferred == (child,)
        assert "render:child" not in log
        # Set before the defer check, so a deferred child's on_load still invalidates.
        assert child._parent is host

    def test_a_deferred_child_still_invalidates_through_its_parent(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        invalidated: list[bool] = []
        runtime = ComponentRuntime(host, on_invalidate=lambda: invalidated.append(True))
        runtime.render(defer=lambda component: component is child)

        child.label = "written by a load"

        assert invalidated

    def test_a_discovery_tree_cannot_be_committed(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)
        tree = runtime.render(defer=lambda component: component is child)

        with pytest.raises(LayoutInvariantError, match="discovery render"):
            runtime.commit(tree)

    def test_no_defer_predicate_expands_everything(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)

        tree = runtime.render()

        assert tree.deferred == ()
        assert "render:child" in log


class Nested(Component):
    """A loading parent that embeds a loading child, for the tiered-pass case."""

    ready: bool = state(default=False)

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.child = Leaf(log, "child")

    async def on_load(self) -> None:
        self.log.append("load:parent")
        self.ready = True

    def render(self):
        self.log.append("render:parent")
        nodes: list[LayoutNode] = [Text("parent")]
        if self.ready:
            nodes.append(self.embed(self.child, key="child"))
        return nodes


class Siblings(Component):
    """Two children that enter the tree together, so their loads share one task group."""

    def __init__(self, log: list[str], *, first: Component, second: Component) -> None:
        self.log = log
        self.first = first
        self.second = second

    def render(self):
        return [self.embed(self.first, key="first"), self.embed(self.second, key="second")]


class TestLoading:
    """`on_load` runs before the first render that would show the component."""

    async def test_the_delivered_render_is_the_loaded_one(self):
        log: list[str] = []
        mount = Mount(Leaf(log, "panel"), timeout=None)
        destination = _Destination(fake_message())

        await mount.send(destination)

        assert log == ["load:panel", "render:panel"]
        assert len(destination.calls) == 1
        view, _files = destination.calls[0]
        assert "panel loaded" in str(view.to_components())
        assert not mount.pending

    async def test_a_child_loads_before_its_own_first_render(self):
        log: list[str] = []
        mount = Mount(Nested(log), timeout=None)

        await mount.send(delivered_to(fake_message()))

        # The parent's loaded render is what reveals the child, so the tiers are serial —
        # but no component renders before its own load.
        assert log.index("load:child") < log.index("render:child")
        assert log.index("load:parent") < log.index("render:parent")

    async def test_siblings_load_together_and_coalesce_into_one_paint(self):
        log: list[str] = []
        started = anyio.Event()

        class Slow(Leaf):
            async def on_load(self) -> None:
                started.set()
                await super().on_load()

        class Waits(Leaf):
            async def on_load(self) -> None:
                # Deadlocks unless the sibling is genuinely in flight at the same time.
                await started.wait()
                await super().on_load()

        component = Siblings(log, first=Waits(log, "waits"), second=Slow(log, "slow"))
        mount = Mount(component, timeout=None)
        destination = _Destination(fake_message())

        with anyio.fail_after(5):
            await mount.send(destination)

        assert len(destination.calls) == 1
        rendered = str(destination.calls[0][0].to_components())
        assert "waits loaded" in rendered
        assert "slow loaded" in rendered

    async def test_a_component_embedded_mid_session_loads_before_the_edit(self):
        log: list[str] = []

        class Opener(Component):
            open: bool = state(default=False)

            def __init__(self) -> None:
                self.child = Leaf(log, "child")

            def render(self):
                nodes: list[LayoutNode] = [Row((Button("open", self.reveal, "open"),))]
                if self.open:
                    nodes.append(self.embed(self.child, key="child"))
                return nodes

            async def reveal(self, event: PressEvent) -> None:
                self.open = True

        mount = Mount(Opener(), timeout=None)
        await mount.send(delivered_to(fake_message()))
        assert log == []

        interaction = fake_interaction()
        await mount.dispatch("open", interaction)

        assert log.index("load:child") < log.index("render:child")
        assert "child loaded" in str(interaction.response.edit_message.await_args.kwargs["view"].to_components())

    async def test_a_failed_load_delivers_nothing_and_stays_retryable(self):
        attempts: list[int] = []

        class Flaky(Component):
            label: str = state("")

            async def on_load(self) -> None:
                attempts.append(1)
                if len(attempts) == 1:
                    message = "the database is down"
                    raise RuntimeError(message)
                self.label = "loaded"

            def render(self):
                return Text(self.label)

        mount = Mount(Flaky(), timeout=None)
        destination = _Destination(fake_message())

        with pytest.raises(RuntimeError, match="the database is down"):
            await mount.send(destination)

        assert destination.calls == []
        assert mount._generation == 0

        await mount.send(destination)

        assert len(attempts) == 2
        assert "loaded" in str(destination.calls[0][0].to_components())

    async def test_a_lone_failure_arrives_unwrapped(self):
        """Error routing downstream of a mount is isinstance-based, not `except*`-based."""

        class Boom(Leaf):
            async def on_load(self) -> None:
                message = "no such account"
                raise LookupError(message)

        log: list[str] = []
        component = Siblings(log, first=Boom(log, "boom"), second=Leaf(log, "fine"))
        mount = Mount(component, timeout=None)

        with pytest.raises(LookupError, match="no such account"):
            await mount.send(_Destination(fake_message()))

    async def test_several_failures_at_once_stay_a_group(self):
        class Boom(Leaf):
            async def on_load(self) -> None:
                await anyio.sleep(0)
                message = f"{self.name} failed"
                raise RuntimeError(message)

        log: list[str] = []
        component = Siblings(log, first=Boom(log, "first"), second=Boom(log, "second"))
        mount = Mount(component, timeout=None)

        with pytest.raises(BaseExceptionGroup) as caught:
            await mount.send(_Destination(fake_message()))

        assert len(caught.value.exceptions) == 2

    async def test_a_completed_load_does_not_run_again(self):
        log: list[str] = []
        component = Leaf(log, "panel")
        mount = Mount(component, timeout=None)
        destination = _Destination(fake_message(), raises=_http_error())

        with pytest.raises(discord.HTTPException):
            await mount.send(destination)
        destination.raises = None
        await mount.send(destination)
        component.label = "changed"
        await mount.refresh_now()

        assert log.count("load:panel") == 1

    async def test_build_view_renders_without_loading(self):
        """The stage-only escape hatch is sync, so it cannot load — and does not pretend to."""
        log: list[str] = []
        mount = Mount(Leaf(log, "panel"), timeout=None)

        mount.build_view()
        await mount.finish(disable=True)

        assert log == ["render:panel"]

    async def test_a_terminal_render_loads_nothing(self):
        log: list[str] = []
        component = Nested(log)
        mount = Mount(component, timeout=None)
        await mount.send(delivered_to(fake_message()))
        component.child = Leaf(log, "late")
        log.clear()

        await mount.finish(disable=True)

        assert not any(entry.startswith("load:") for entry in log)

    async def test_a_tree_declaring_no_loads_takes_no_extra_render(self):
        renders: list[int] = []

        class Plain(Component):
            count: int = state(0)

            def render(self):
                renders.append(self.count)
                return Text(f"count: {self.count}")

        mount = Mount(Plain(), timeout=None)

        await mount.send(delivered_to(fake_message()))

        assert len(renders) == 1

    async def test_a_load_writes_plain_state_outside_any_transaction(self):
        """Plan 08's tracking covers handlers; a load is ordinary pre-delivery state."""
        seen: list[bool] = []

        class Reader(Component):
            label: str = state("")

            async def on_load(self) -> None:
                seen.append(_CURRENT.get() is not None)
                self.untracked = "a plain attribute, written with nothing watching"
                self.label = "loaded"

            def render(self):
                return Text(self.label)

        mount = Mount(Reader(), timeout=None)
        await mount.send(delivered_to(fake_message()))

        assert seen == [False]

    async def test_a_load_that_never_settles_is_reported(self):
        class Endless(Component):
            depth: int = state(0)

            def __init__(self) -> None:
                self.child: Endless | None = None

            async def on_load(self) -> None:
                self.child = Endless()
                self.child.depth = self.depth + 1

            def render(self):
                nodes: list[LayoutNode] = [Text(f"depth {self.depth}")]
                if self.child is not None:
                    nodes.append(self.embed(self.child, key="child"))
                return nodes

        mount = Mount(Endless(), timeout=None)

        with pytest.raises(LayoutInvariantError, match="did not settle"):
            await mount.send(delivered_to(fake_message()))
