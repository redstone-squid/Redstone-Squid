"""Direct and keyed children share expansion, caching, and ownership semantics."""

from typing import override

import pytest

import squid_ui as ui
from squid_ui.runtime import ComponentRuntime
from squid_ui.runtime.component import render_component_tree
from squid_ui.testing import keys, texts


class Counter(ui.Component):
    value: int = ui.state(0)

    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    def render(self) -> ui.LayoutNode:
        return ui.block(
            ui.paragraph(str(self.value)),
            ui.action_controls(ui.action_control("Increment", self.increment, key="increment"), key="actions"),
        )

    async def increment(self, _event: ui.PressEvent) -> None:
        self.value += 1

    @override
    def on_mount(self) -> None:
        self.events.append("mount")

    @override
    def on_unmount(self) -> None:
        self.events.append("unmount")


class Parent(ui.Component):
    children: tuple[ui.LayoutNode, ...] = ui.state((), opaque=True)

    def __init__(self, *children: ui.LayoutNode) -> None:
        self.children = children

    @override
    def render(self) -> ui.LayoutNode:
        return ui.block(*self.children)


def _commit(runtime: ComponentRuntime) -> None:
    runtime.commit(runtime.render(reuse_committed=True))


@pytest.mark.parametrize("explicit", [False, True])
def test_reordering_retains_namespaces_state_and_mounts(explicit: bool) -> None:
    first, second = Counter(), Counter()
    children = (first.keyed("first"), second.keyed("second")) if explicit else (first, second)
    parent = Parent(*children)
    runtime = ComponentRuntime(parent)
    before = runtime.render()
    runtime.commit(before)
    paths = {component: path for path, component in before.components.items()}

    first.value = 3
    parent.children = tuple(reversed(children))
    after = runtime.render(reuse_committed=True)
    runtime.commit(after)

    assert texts(after.nodes) == ["0", "3"]
    assert {component: path for path, component in after.components.items()} == paths
    assert set(keys(after.nodes)) == set(keys(before.nodes))
    assert first.events == second.events == ["mount"]

    # A dirty child can be spliced after the parent reordered its layout.
    first.value = 4
    updated = runtime.render(reuse_committed=True)
    assert texts(updated.nodes) == ["0", "4"]
    runtime.commit(updated)

    parent.children = (children[0],)
    _commit(runtime)
    assert first.events == ["mount"]
    assert second.events == ["mount", "unmount"]


def test_keyed_reference_does_not_change_direct_placement() -> None:
    child = Counter()
    direct_path = next(path for path in render_component_tree(Parent(child)).components if path != "$")
    reference = child.keyed(42)
    assert reference.component is child
    assert reference.key == "42"
    assert child.keyed("other").component is child
    assert next(path for path in render_component_tree(Parent(child)).components if path != "$") == direct_path


def test_a_new_instance_at_the_same_key_gets_its_own_state_and_lifecycle() -> None:
    old, new = Counter(), Counter()
    old.value = 7
    parent = Parent(old.keyed("editor"))
    runtime = ComponentRuntime(parent)
    _commit(runtime)

    parent.children = (new.keyed("editor"),)
    replacement = runtime.render(reuse_committed=True)
    runtime.commit(replacement)

    assert replacement.components["editor"] is new
    assert texts(replacement.nodes) == ["0"]
    assert old.events == ["mount", "unmount"]
    assert new.events == ["mount"]


def test_direct_child_discovery_defers_until_loaded() -> None:
    child = Counter()
    runtime = ComponentRuntime(Parent(child))
    discovery = runtime.render(defer=lambda component: component is child, reuse_committed=True)
    assert discovery.deferred == (child,)
    assert texts(discovery.nodes) == []

    child.value = 5
    delivered = runtime.render(reuse_committed=True)
    runtime.commit(delivered)
    assert texts(delivered.nodes) == ["5"]
    assert child.events == ["mount"]


@pytest.mark.parametrize("key", ["", "contains.dot"])
def test_invalid_explicit_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="Boundary key"):
        Counter().keyed(key)


def test_duplicate_keys_are_rejected_across_nested_containers() -> None:
    parent = Parent(Counter().keyed("same"), ui.block(Counter().keyed("same")))
    with pytest.raises(ui.LayoutInvariantError, match="duplicate Boundary key"):
        render_component_tree(parent)


@pytest.mark.parametrize("explicit", [False, True])
def test_one_instance_cannot_appear_twice(explicit: bool) -> None:
    child = Counter()
    parent = Parent(child.keyed("first"), child.keyed("second")) if explicit else Parent(child, child)
    with pytest.raises(ui.LayoutInvariantError, match=r"already embedded|duplicate Boundary key"):
        render_component_tree(parent)


def test_direct_cycles_are_rejected() -> None:
    parent = Parent()
    parent.children = (parent,)
    with pytest.raises(ui.LayoutInvariantError, match="embedding cycle"):
        render_component_tree(parent)


def test_component_can_return_a_child_without_a_container() -> None:
    child = Counter()

    class Wrapper(ui.Component):
        @override
        def render(self) -> ui.LayoutNode:
            return child

    assert texts(render_component_tree(Wrapper()).nodes) == ["0"]
