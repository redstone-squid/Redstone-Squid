"""Visibility follows delivered fragments, independently of logical instance lifetime."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_ui.planning import plan
from squid_ui.runtime import ComponentRuntime


class Editor(sl.Component):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @override
    def render(self) -> sl.LayoutNode:
        return sl.paragraph("x" * 40)

    @override
    def on_mount(self) -> None:
        self.events.append("mount")

    @override
    def on_show(self, placement: str) -> None:
        self.events.append(f"show:{placement}")

    @override
    def on_hide(self, placement: str) -> None:
        self.events.append(f"hide:{placement}")

    @override
    def on_unmount(self) -> None:
        self.events.append("unmount")


class Screen(sl.Component):
    included = sl.state(True)

    def __init__(self, events: list[str]) -> None:
        self.editor = Editor(events)

    @override
    def render(self) -> sl.LayoutNode:
        if not self.included:
            return sl.paragraph("closed")
        return sl.paged(
            sl.stack(
                sl.unbreakable(self.editor.keyed("left")),
                sl.unbreakable(self.editor.keyed("right")),
            ),
            key="body",
            chars=50,
        )


def test_shared_instance_visibility_changes_on_a_page_turn_without_unmounting() -> None:
    events: list[str] = []
    screen = Screen(events)
    runtime = ComponentRuntime(screen)
    tree = runtime.render()
    first = plan(tree.nodes, target=html.target())
    assert first.visible_components == frozenset({"left"})
    assert first.visible_components is not None
    runtime.commit(tree, visible={"$", *first.visible_components})
    assert events == ["mount", "show:left"]
    assert set(runtime.components) == {"$", "left", "right"}
    assert set(runtime.visible_components) == {"$", "left"}
    assert first.allocation is not None
    region = next(iter(first.allocation.regions))
    second = first.for_page({region: 1})
    assert second.visible_components == frozenset({"right"})
    assert second.visible_components is not None

    runtime.stage(tree).discard()
    assert events == ["mount", "show:left"]
    runtime.commit(tree, visible={"$", *second.visible_components})
    assert events == ["mount", "show:left", "hide:left", "show:right"]
    runtime.commit(tree, visible={"$", *second.visible_components})
    assert events == ["mount", "show:left", "hide:left", "show:right"]

    screen.included = False
    closed = runtime.render()
    runtime.commit(closed, visible={"$"})
    assert events[-2:] == ["hide:right", "unmount"]
    runtime.finish()


def test_finish_hides_each_shared_placement_before_unmounting_once() -> None:
    events: list[str] = []
    runtime = ComponentRuntime(Screen(events))
    runtime.commit(runtime.render())
    assert events[0] == "mount"
    assert set(events[1:]) == {"show:left", "show:right"}
    runtime.finish()
    assert set(events[-3:-1]) == {"hide:left", "hide:right"}
    assert events[-1] == "unmount"
    assert not runtime.visible_components


def test_an_unknown_visible_placement_does_not_settle_the_candidate() -> None:
    runtime = ComponentRuntime(Screen([]))
    candidate = runtime.stage(runtime.render())
    with pytest.raises(sl.errors.LayoutInvariantError, match="visible placements"):
        candidate.commit(visible={"not-declared"})
    assert not candidate.settled
    candidate.discard()
    runtime.finish()
