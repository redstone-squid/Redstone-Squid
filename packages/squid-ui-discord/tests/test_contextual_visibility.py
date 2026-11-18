"""Declared contextual slots reconcile visibility only after actual delivery."""

from typing import override

import discord
import pytest

import squid_ui as sl
from squid_ui.ext.discord import Everyone, MessageRoot
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.testing import delivered_to, message_harness
from squid_ui.selectors import query, select


async def test_hidden_prepared_slot_mounts_but_visibility_waits_for_successful_delivery() -> None:
    events: list[str] = []

    class Editor(sl.Component):
        @override
        async def on_load(self) -> None:
            events.append("load")

        @override
        def on_mount(self) -> None:
            events.append("mount")

        @override
        def on_show(self, placement: str) -> None:
            events.append(f"show:{placement}")

        @override
        def on_hide(self, placement: str) -> None:
            events.append(f"hide:{placement}")

        @override
        def on_unmount(self) -> None:
            events.append("unmount")

        @override
        def render(self) -> sl.LayoutNode:
            return sl.paragraph("editor")

    class Screen(sl.Component):
        shown = sl.state(False)

        def __init__(self) -> None:
            self.editor = Editor()

        @override
        def render(self) -> sl.LayoutNode:
            shown = self.shown
            editor = self.editor.keyed("editor")
            return sl.stack(
                "root", sl.context(lambda _ctx: editor if shown else (), key="layout", components=(editor,))
            )

    screen = Screen()
    root = MessageRoot(screen, access=Everyone(), timeout=None)
    message = message_harness()
    await root.send(delivered_to(message))
    assert events == ["load", "mount"]
    assert "editor" in root.runtime.components
    assert "editor" not in root.runtime.visible_components
    assert query(root.runtime.queries, select(Editor)).count() == 1

    screen.shown = True
    await root.refresh()
    assert events == ["load", "mount", "show:editor"]
    assert "editor" in root.runtime.visible_components

    screen.shown = False
    message.edit.error = sd.http_error(message="delivery refused")
    with pytest.raises(discord.HTTPException):
        await root.refresh()
    assert events == ["load", "mount", "show:editor"]
    assert "editor" in root.runtime.visible_components
    message.edit.error = None
    await root.refresh()
    assert events == ["load", "mount", "show:editor", "hide:editor"]
    assert "editor" in root.runtime.components
    assert "editor" not in root.runtime.visible_components
    await root.finish()
    assert events[-1] == "unmount"


async def test_generated_handlers_capture_the_final_selected_view() -> None:
    remembered: list[int] = []

    class Screen(sl.Component):
        @override
        def render(self) -> sl.LayoutNode:
            def controls(ctx: sl.LayoutContext) -> sl.LayoutNode:
                selected = ctx.region(sl.region_ref("results")).selection.require()
                assert selected is not None
                number = selected.number.require()

                async def remember(event: sl.PressEvent) -> None:
                    remembered.append(number)
                    await event.acknowledge()

                return sl.action_controls(
                    sl.action_control(f"Remember {number}", remember, key="remember"), key="actions"
                )

            return sl.stack(
                sl.bullets(*(sl.bullet(str(i), key=f"item-{i}") for i in range(5)), key="results", group_size=2),
                sl.context(controls, key="controls"),
            )

    root = MessageRoot(Screen(), access=Everyone(), timeout=None)
    await root.send(delivered_to(message_harness()))
    first_button = query(root.runtime.queries, select(sl.semantic.ActionControl).label("Remember 1"))
    captured = first_button.capture()
    assert captured.count() == 1
    await root.dispatch("controls.remember", sd.interaction_harness())
    assert remembered == [1]
    root.presentation.request_cursor("results", sl.planning.PagePosition(page=1))
    await root.refresh()
    await root.dispatch("controls.remember", sd.interaction_harness())
    assert remembered == [1, 2]
    assert not first_button.exists()
    assert captured.count() == 1
    assert query(root.runtime.queries, select(sl.semantic.ActionControl).label("Remember 2")).count() == 1
    await root.finish()
