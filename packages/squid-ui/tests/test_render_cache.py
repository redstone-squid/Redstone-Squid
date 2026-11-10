"""Cache reconciliation must preserve changes that happen around delivery."""

from typing import override

import pytest

import squid_ui as ui
from squid_ui import testing
from squid_ui.runtime import ComponentRuntime


class Counter(ui.Component):
    value: int = ui.state(0)

    @override
    def render(self) -> ui.LayoutNode:
        return ui.paragraph(str(self.value))


class MountingCounter(Counter):
    @override
    def on_mount(self) -> None:
        self.value += 1


class Switch(ui.Component):
    shown: bool = ui.state(default=False)

    def __init__(self, child: ui.Component) -> None:
        self.child = child

    @override
    def render(self) -> ui.LayoutNode:
        return self.boundary(self.child, key="child") if self.shown else ui.paragraph("hidden")


class Root(ui.Component):
    def __init__(self, *children: ui.Component) -> None:
        self.children = children

    @override
    def render(self) -> ui.LayoutNode:
        return ui.block(*(self.boundary(child, key=str(index)) for index, child in enumerate(self.children)))


def commit(runtime: ComponentRuntime, *, incremental: bool = True) -> None:
    if not incremental:
        runtime.invalidate()
    tree = runtime.render(reuse_committed=True)
    runtime.commit(tree)


@pytest.mark.parametrize("incremental", [False, True])
def test_new_child_mount_writes_survive_cache_reconciliation(incremental: bool) -> None:
    child = MountingCounter()
    switch = Switch(child)
    runtime = ComponentRuntime(Root(switch, Counter()))
    commit(runtime)

    switch.shown = True
    commit(runtime, incremental=incremental)

    assert runtime.dirty
    assert child.value == 1
    refreshed = runtime.render(reuse_committed=True)
    assert testing.texts(refreshed.nodes) == ["1", "0"]
    runtime.commit(refreshed)
    assert not runtime.dirty


@pytest.mark.parametrize("incremental", [False, True])
def test_removed_child_hook_invalidates_a_retained_sibling(incremental: bool) -> None:
    sibling = Counter()

    class Child(Counter):
        @override
        def on_unmount(self) -> None:
            sibling.value += 1

    switch = Switch(Child())
    switch.shown = True
    runtime = ComponentRuntime(Root(switch, sibling))
    commit(runtime)

    switch.shown = False
    commit(runtime, incremental=incremental)

    assert runtime.dirty
    assert testing.texts(runtime.render(reuse_committed=True).nodes) == ["hidden", "1"]


def test_failed_render_keeps_explicit_invalidation_until_retry() -> None:
    class Unstable(Counter):
        fail = False
        label = "initial"

        @override
        def render(self) -> ui.LayoutNode:
            if self.fail:
                message = "render failed"
                raise ValueError(message)
            return ui.paragraph(f"{self.label}:{self.value}")

    child = Unstable()
    runtime = ComponentRuntime(Root(child))
    commit(runtime)
    child.fail = True
    child.invalidate()
    with pytest.raises(ValueError, match="render failed"):
        runtime.render(reuse_committed=True)

    child.fail = False
    child.label = "recovered"
    assert testing.texts(runtime.render(reuse_committed=True).nodes) == ["recovered:0"]


def test_deferred_discovery_can_expand_the_child_on_retry() -> None:
    child = Counter()
    runtime = ComponentRuntime(Root(child))
    discovery = runtime.render(defer=lambda component: component is child, reuse_committed=True)
    assert discovery.deferred == (child,)
    with pytest.raises(ui.LayoutInvariantError, match="discovery render"):
        runtime.commit(discovery)

    child.value = 3
    complete = runtime.render(reuse_committed=True)
    assert not complete.deferred
    assert testing.texts(complete.nodes) == ["3"]
