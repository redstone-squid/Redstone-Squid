"""Runtime inspection cannot bypass invalidation or component lifecycle."""

from typing import override

import pytest

import squid_ui as ui
from squid_ui import testing
from squid_ui.context_keys import ContextKey
from squid_ui.ext.discord.primitives.nodes import Boundary
from squid_ui.runtime import ComponentRuntime

LABEL = ContextKey[str]("label")


class Label(ui.Component):
    @override
    def render(self) -> ui.LayoutNode:
        return ui.paragraph(self.inject(LABEL))


class Switch(ui.Component):
    shown: bool = ui.state(False)

    def __init__(self, child: ui.Component) -> None:
        self.child = child

    @override
    def render(self) -> ui.LayoutNode:
        return self.child.keyed("child") if self.shown else ui.paragraph("hidden")


def test_context_view_tracks_supported_updates_and_render_invalidation() -> None:
    supplied = {LABEL: "initial"}
    runtime = ComponentRuntime(Label(), context=supplied)
    context = runtime.context
    runtime.commit(runtime.render())
    revision = runtime.revision
    assert not runtime.dirty

    supplied[LABEL] = "outside"
    assert context[LABEL] == "initial"
    with pytest.raises(TypeError):
        context[LABEL] = "bypass"  # type: ignore  # Exercise runtime rejection of an invalid write.
    with pytest.raises(TypeError):
        del context[LABEL]  # type: ignore  # Exercise runtime rejection of an invalid deletion.
    assert not runtime.dirty
    assert runtime.revision == revision

    runtime.set_context(LABEL, "updated")
    assert context[LABEL] == "updated"
    assert runtime.dirty
    assert runtime.revision == revision + 1
    tree = runtime.render(reuse_committed=True)
    assert testing.texts(tree.nodes) == ["updated"]
    runtime.commit(tree)
    assert not runtime.dirty
    runtime.finish()


@pytest.mark.parametrize("incremental", [False, True])
def test_component_view_tracks_only_committed_placements_and_finish(incremental: bool) -> None:
    child = Label()
    root = Switch(child)
    runtime = ComponentRuntime(root, context={LABEL: "child"})
    components = runtime.components
    assert components == {}
    runtime.commit(runtime.render())
    assert components == {"$": root}

    with pytest.raises(TypeError):
        components["$"] = child  # type: ignore  # Exercise runtime rejection of an invalid write.
    with pytest.raises(TypeError):
        del components["$"]  # type: ignore  # Exercise runtime rejection of an invalid deletion.
    assert components == {"$": root}

    root.shown = True
    if not incremental:
        runtime.invalidate()
    candidate = runtime.render(reuse_committed=True)
    assert components == {"$": root}
    runtime.commit(candidate)
    assert set(components.values()) == {root, child}
    assert components == candidate.components

    root.shown = False
    if not incremental:
        runtime.invalidate()
    runtime.commit(runtime.render(reuse_committed=True))
    assert components == {"$": root}
    runtime.finish()
    assert components == {}


@pytest.mark.parametrize("attribute", ["context", "components"])
def test_runtime_inspection_mappings_cannot_be_reassigned_or_deleted(attribute: str) -> None:
    runtime = ComponentRuntime(Label(), context={LABEL: "initial"})
    with pytest.raises(AttributeError):
        setattr(runtime, attribute, {})
    with pytest.raises(AttributeError):
        delattr(runtime, attribute)
    runtime.finish()


def test_render_retains_authored_boundaries_and_local_keys() -> None:
    child = Label()
    root = Switch(child)
    runtime = ComponentRuntime(root, context={LABEL: "child"})
    root.shown = True
    tree = runtime.render()
    boundary = tree.authored_nodes["$"][0]
    assert isinstance(boundary, Boundary)
    assert boundary.component is child
    assert testing.texts(tree.authored_nodes["child"]) == ["child"]
    with pytest.raises(TypeError):
        tree.authored_nodes["$"] = ()  # type: ignore  # Inspection must not rewrite a render.
    runtime.commit(tree)
    root.shown = False
    updated = runtime.render(reuse_committed=True)
    assert set(updated.authored_nodes) == {"$"}
    assert set(tree.authored_nodes) == {"$", "child"}
    runtime.finish()
