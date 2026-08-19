"""Reactive core tests: state, dispatch funnel, flush, lifecycle."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from squid_layouts import (
    Button,
    Component,
    Heading,
    Mount,
    Option,
    Reactor,
    Row,
    SelectMenu,
    Text,
    assert_within_limits,
    state,
)
from squid_layouts.testing import fake_interaction


class Counter(Component):
    count: int = state(0)

    def render(self):
        return [
            Heading("Counter"),
            Text(f"count: {self.count}"),
            Row((Button(label="+1", on_click=self.increment, key="inc"),)),
        ]

    async def increment(self, interaction) -> None:
        self.count += 1


def _button(view: discord.ui.LayoutView) -> discord.ui.Button:
    return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))


class TestRenderAndWire:
    def test_build_view_wires_handlers(self):
        mount = Mount(Counter(), timeout=None)
        view = mount.build_view()
        button = _button(view)
        assert button.custom_id is not None and button.custom_id.startswith(f"ctl:{mount.id}:inc")
        assert "inc" in mount._handlers
        assert_within_limits(view)

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

    async def test_clean_dispatch_defers_instead_of_editing(self):
        class Static(Counter):
            async def increment(self, interaction) -> None:
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

            async def pick(self, interaction, values) -> None:
                picked.extend(values)

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
