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


def test_repeated_direct_children_need_explicit_placement_keys() -> None:
    child = Counter()
    with pytest.raises(ui.LayoutInvariantError, match="duplicate Boundary key"):
        render_component_tree(Parent(child, child))


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


def test_shared_placements_update_together_and_mount_once() -> None:
    child = Counter()
    parent = Parent(child.keyed("first"), child.keyed("second"))
    runtime = ComponentRuntime(parent)
    _commit(runtime)
    assert child.events == ["mount"]
    child.value = 5
    assert texts(runtime.render(reuse_committed=True).nodes) == ["5", "5"]
    _commit(runtime)

    parent.children = (child.keyed("second"),)
    _commit(runtime)
    assert child.events == ["mount"]
    child.value = 6
    assert texts(runtime.render(reuse_committed=True).nodes) == ["6"]
    _commit(runtime)
    runtime.finish()
    assert child.events == ["mount", "unmount"]
    assert child._runtime is None


def test_shared_placements_render_and_cache_their_own_contexts() -> None:
    label = ui.ContextKey[str]("label")

    class Reader(Counter):
        @override
        def render(self) -> ui.LayoutNode:
            return ui.paragraph(f"{self.inject(label)}:{self.value}")

    class Provider(Parent):
        caption: str = ui.state("")

        def __init__(self, caption: str, child: ui.Component) -> None:
            super().__init__(child)
            self.caption = caption

        @override
        def render(self) -> ui.LayoutNode:
            self.provide(label, self.caption)
            return super().render()

    reader = Reader()
    branch = Parent(reader)
    first, second = Provider("one", branch), Provider("two", branch)
    runtime = ComponentRuntime(Parent(first, second))
    _commit(runtime)
    assert reader.events == ["mount"]
    reader.value = 3
    assert texts(runtime.render(reuse_committed=True).nodes) == ["one:3", "two:3"]
    _commit(runtime)
    first.caption = "changed"
    assert texts(runtime.render(reuse_committed=True).nodes) == ["changed:3", "two:3"]
    _commit(runtime)
    reader.invalidate()
    assert texts(runtime.render(reuse_committed=True).nodes) == ["changed:3", "two:3"]
    runtime.finish()
    assert reader.events == ["mount", "unmount"]


def test_discarding_a_placement_removal_keeps_the_instance_mounted() -> None:
    child = Counter()
    parent = Parent(child.keyed("one"), child.keyed("two"))
    runtime = ComponentRuntime(parent)
    _commit(runtime)
    parent.children = ()
    runtime.render(reuse_committed=True)
    runtime.discard()
    assert child._runtime is runtime
    assert child.events == ["mount"]
    runtime.finish()
    assert child.events == ["mount", "unmount"]


def test_instances_cannot_be_claimed_by_two_runtimes() -> None:
    child = Counter()
    first = ComponentRuntime(Parent(child))
    second = ComponentRuntime(Parent(child))
    first.render()
    with pytest.raises(ui.LayoutInvariantError, match="multiple runtimes"):
        second.render()
    assert child._runtime is first
    first.discard()
    _commit(second)
    assert child._runtime is second
    second.finish()
    first.finish()


def test_adding_and_removing_alias_placements_does_not_restart_lifecycle() -> None:
    child = Counter()
    branch = Parent(child.keyed("one"))
    runtime = ComponentRuntime(Parent(branch))
    _commit(runtime)
    branch.children = (child.keyed("two"),)
    _commit(runtime)
    branch.children = (child.keyed("two"), child.keyed("three"))
    _commit(runtime)
    assert child.events == ["mount"]
    child.value = 8
    assert texts(runtime.render(reuse_committed=True).nodes) == ["8", "8"]
    _commit(runtime)
    branch.children = ()
    _commit(runtime)
    assert child.events == ["mount", "unmount"]
    assert child._runtime is None


def test_new_placement_observes_shared_state_written_by_a_mount_hook() -> None:
    child = Counter()
    parent = Parent(child.keyed("first"))
    runtime = ComponentRuntime(parent)
    _commit(runtime)

    class Writer(Parent):
        @override
        def on_mount(self) -> None:
            child.value = 9

    parent.children = (child.keyed("first"), child.keyed("second"), Writer())
    _commit(runtime)
    assert runtime.dirty
    updated = runtime.render(reuse_committed=True)
    assert texts(updated.nodes) == ["9", "9"]
    runtime.commit(updated)
    runtime.finish()


def test_state_changes_reach_placements_in_a_superseded_candidate() -> None:
    child = Counter()
    parent = Parent(child.keyed("one"))
    runtime = ComponentRuntime(parent)
    _commit(runtime)
    parent.children = (child.keyed("one"), child.keyed("two"))
    runtime.render(reuse_committed=True)
    child.value = 4
    replacement = runtime.render(reuse_committed=True)
    assert texts(replacement.nodes) == ["4", "4"]
    runtime.commit(replacement)
    runtime.finish()


def test_failed_stage_preserves_previous_candidate_and_releases_new_claims() -> None:
    existing, available, foreign = Counter(), Counter(), Counter()
    runtime = ComponentRuntime(Parent(existing))
    candidate = runtime.stage(runtime.render())
    other = ComponentRuntime(Parent(foreign))
    _commit(other)
    prospective = render_component_tree(Parent(available, foreign))
    with pytest.raises(ui.LayoutInvariantError, match="multiple runtimes"):
        runtime.stage(prospective)
    assert available._runtime is None
    assert foreign._runtime is other
    candidate.commit()
    assert existing.events == ["mount"]
    runtime.finish()
    other.finish()
