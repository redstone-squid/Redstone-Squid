"""Composable components: embedding, key namespacing, and where invalidation travels."""

from collections.abc import Callable

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

import squid_ui as sl
import squid_ui.runtime.component as component_module
import squid_ui.runtime.owner as owner_module
import squid_ui_discord as sd
from squid_ui import Component, ContextKey, PressEvent, state
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives import (
    Boundary,
    Break,
    Budget,
    Button,
    Card,
    Heading,
    Lines,
    Node,
    Paginate,
    Panel,
    Row,
    Text,
)
from squid_ui.runtime.component import render_component_tree
from squid_ui.runtime.owner import ComponentRuntime
from squid_ui.runtime.shared import SharedState
from squid_ui.runtime.topics import CellAddress, LocalTopicBus
from squid_ui.semantic import ActionControl, ActionControls, Choice, Choices, Controlled, Group, List, ListItem
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import commit_render, fake_interaction


class Counter(Component):
    count: int = state(0)

    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return [
            Text(f"{self.name}: {self.count}"),
            Row(
                (
                    Button(label="+1", on_click=self.increment, key="inc"),
                    Button(label="?", on_click=self.noop, key="help"),
                )
            ),
        ]

    async def increment(self, event: PressEvent) -> None:
        self.count += 1

    async def noop(self, event: PressEvent) -> None: ...


class Pair(Component):
    """The shape that used to cross-wire: two instances of one child class."""

    def __init__(self) -> None:
        self.left = Counter("left")
        self.right = Counter("right")

    def render(self):
        return [Heading("Pair"), self.boundary(self.left, key="left"), self.boundary(self.right, key="right")]


def _custom_ids(view: discord.ui.LayoutView) -> list[str]:
    return [item.custom_id or "" for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class TestBoundaries:
    async def test_each_instance_answers_only_its_own_control(self):
        pair = Pair()
        message_root = MessageRoot(pair, access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("left.inc", fake_interaction())

        assert (pair.left.count, pair.right.count) == (1, 0)

        await message_root.dispatch("right.inc", fake_interaction())

        assert (pair.left.count, pair.right.count) == (1, 1)

    def test_controls_are_namespaced_including_every_explicit_key(self):
        message_root = MessageRoot(Pair(), access=Everyone(), timeout=None)
        commit_render(message_root)
        assert set(message_root._handlers) == {"left.inc", "left.help", "right.inc", "right.help"}

    def test_a_childs_state_change_re_renders_the_root_message(self):
        pair = Pair()
        message_root = MessageRoot(pair, access=Everyone(), timeout=None)
        commit_render(message_root)

        pair.right.count = 3

        assert message_root._dirty
        assert "right: 3" in _texts(commit_render(message_root))

    def test_components_do_not_expose_the_frontend_root(self):
        pair = Pair()
        commit_render(MessageRoot(pair, access=Everyone(), timeout=None))
        assert not hasattr(pair.left, "mount")

    def test_embedding_does_not_mutate_the_childs_own_keys(self):
        # render() stays pure: namespacing rewrites the returned tree, not the component.
        pair = Pair()
        commit_render(MessageRoot(pair, access=Everyone(), timeout=None))
        row = pair.left.render()[1]
        assert isinstance(row, Row)
        button = row.items[0]
        assert isinstance(button, Button)
        assert button.key == "inc"


class TestRenderCaching:
    def test_dirty_leaf_splices_without_visiting_clean_siblings(self, monkeypatch) -> None:
        class Leaf(Component):
            value: int = state(0)

            def render(self) -> Text:
                return Text(str(self.value))

        class Root(Component):
            def __init__(self) -> None:
                self.leaves = tuple(Leaf() for _ in range(1_000))

            def render(self):
                return tuple(self.boundary(leaf, key=str(index)) for index, leaf in enumerate(self.leaves))

        root = Root()
        runtime = ComponentRuntime(root)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        namespaces = 0
        namespace = component_module._namespace

        def counted_namespace(*args, **kwargs):
            nonlocal namespaces
            namespaces += 1
            return namespace(*args, **kwargs)

        monkeypatch.setattr(component_module, "_namespace", counted_namespace)
        root.leaves[500].value = 1

        changed = runtime.render(reuse_committed=True)

        assert changed.nodes[499:502] == (Text("0"), Text("1"), Text("0"))
        assert namespaces == 1

    def test_dirty_nested_leaf_splices_through_structural_ancestors(self, monkeypatch) -> None:
        class Leaf(Component):
            value: int = state(0)

            def render(self) -> Text:
                return Text(str(self.value))

        class Middle(Component):
            def __init__(self) -> None:
                self.leaf = Leaf()

            def render(self) -> Panel:
                return Panel(children=(Text("before"), self.boundary(self.leaf, key="leaf"), Text("after")))

        class Root(Component):
            def __init__(self) -> None:
                self.middle = Middle()

            def render(self):
                return self.boundary(self.middle, key="middle")

        root = Root()
        runtime = ComponentRuntime(root)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        namespaces = 0
        namespace = component_module._namespace

        def counted_namespace(*args, **kwargs):
            nonlocal namespaces
            namespaces += 1
            return namespace(*args, **kwargs)

        monkeypatch.setattr(component_module, "_namespace", counted_namespace)
        root.middle.leaf.value = 1

        changed = runtime.render(reuse_committed=True)

        assert changed.nodes == (Panel(children=(Text("before"), Text("1"), Text("after"))),)
        assert namespaces == 2

    def test_one_changed_leaf_does_not_render_its_parent_or_sibling(self) -> None:
        class Counting(Component):
            value: int = state(0)

            def __init__(self, label: str) -> None:
                self.label = label
                self.renders = 0

            def render(self) -> Text:
                self.renders += 1
                return Text(f"{self.label}:{self.value}")

        class Root(Component):
            def __init__(self) -> None:
                self.left = Counting("left")
                self.right = Counting("right")
                self.renders = 0

            def render(self):
                self.renders += 1
                return (self.boundary(self.left, key="left"), self.boundary(self.right, key="right"))

        root = Root()
        runtime = ComponentRuntime(root)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)

        root.left.value = 1
        changed = runtime.render(reuse_committed=True)

        assert root.renders == 1
        assert root.left.renders == 2
        assert root.right.renders == 1
        assert changed.nodes == (Text("left:1"), Text("right:0"))

    def test_computed_backdating_keeps_the_render_snapshot(self) -> None:
        class Parity(Component):
            source: int = state(0)

            def __init__(self) -> None:
                self.renders = 0

            @sl.computed
            def even(self) -> bool:
                return self.source % 2 == 0

            def render(self) -> Text:
                self.renders += 1
                return Text(str(self.even))

        component = Parity()
        runtime = ComponentRuntime(component)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)

        component.source = 2
        unchanged = runtime.render(reuse_committed=True)

        assert component.renders == 1
        assert unchanged.nodes == initial.nodes

    def test_address_invalidation_backdates_before_tree_expansion(self, monkeypatch) -> None:
        class Values(SharedState[object]):
            value: int = state(0)

        values = Values(LocalTopicBus(), object())

        class Parity(Component):
            def __init__(self) -> None:
                self.renders = 0

            @sl.computed
            def even(self) -> bool:
                return values.value % 2 == 0

            def render(self) -> Text:
                self.renders += 1
                return Text(str(self.even))

        component = Parity()
        runtime = ComponentRuntime(component)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        committed_components = runtime.components
        values.value = 2

        runtime.invalidate_address(CellAddress(values, "value"))

        def unexpected_expansion(*_args, **_kwargs):
            message = "fully backdated invalidations must not walk the component tree"
            raise AssertionError(message)

        monkeypatch.setattr(owner_module, "render_component_tree", unexpected_expansion)
        unchanged = runtime.render(reuse_committed=True)

        assert component.renders == 1
        assert unchanged is initial
        runtime.commit(unchanged, rendered_revision=runtime.revision)
        assert runtime.components is committed_components

    def test_equal_computed_value_refreshes_changed_dependency_addresses(self) -> None:
        class Values(SharedState[object]):
            use_b: bool = state(default=False)
            a: int = state(1)
            b: int = state(1)

        values = Values(LocalTopicBus(), object())

        class Panel(Component):
            def __init__(self) -> None:
                self.renders = 0

            @sl.computed
            def value(self) -> int:
                return values.b if values.use_b else values.a

            def render(self) -> Text:
                self.renders += 1
                return Text(str(self.value))

        component = Panel()
        runtime = ComponentRuntime(component)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        use_b = CellAddress(values, "use_b")
        a = CellAddress(values, "a")
        b = CellAddress(values, "b")
        assert initial.observations == (use_b, a)

        values.use_b = True
        runtime.invalidate_address(use_b)
        metadata_only = runtime.render(reuse_committed=True)

        assert component.renders == 1
        assert metadata_only is not initial
        assert metadata_only.nodes == initial.nodes
        assert metadata_only.observations == (use_b, b)
        runtime.commit(metadata_only, rendered_revision=runtime.revision)

        values.b = 2
        runtime.invalidate_address(b)
        changed = runtime.render(reuse_committed=True)

        assert component.renders == 2
        assert changed.nodes == (Text("2"),)

    def test_address_invalidation_only_renders_the_dependent_sibling(self) -> None:
        class Values(SharedState[object]):
            left: int = state(0)
            right: int = state(0)

        values = Values(LocalTopicBus(), object())

        class Value(Component):
            def __init__(self, name: str) -> None:
                self.name = name
                self.renders = 0

            def render(self) -> Text:
                self.renders += 1
                return Text(str(getattr(values, self.name)))

        class Root(Component):
            def __init__(self) -> None:
                self.left = Value("left")
                self.right = Value("right")
                self.renders = 0

            def render(self):
                self.renders += 1
                return (self.boundary(self.left, key="left"), self.boundary(self.right, key="right"))

        root = Root()
        runtime = ComponentRuntime(root)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        values.left = 1

        runtime.invalidate_address(CellAddress(values, "left"))
        changed = runtime.render(reuse_committed=True)

        assert root.renders == 1
        assert root.left.renders == 2
        assert root.right.renders == 1
        assert changed.nodes == (Text("1"), Text("0"))

    def test_explicit_invalidation_rerenders_opaque_inputs(self) -> None:
        class Opaque(Component):
            def __init__(self) -> None:
                self.value = "first"
                self.renders = 0

            def render(self) -> Text:
                self.renders += 1
                return Text(self.value)

        component = Opaque()
        runtime = ComponentRuntime(component)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        component.value = "second"
        component.invalidate()

        changed = runtime.render(reuse_committed=True)

        assert component.renders == 2
        assert changed.nodes == (Text("second"),)

    def test_inline_component_construction_keeps_replacement_semantics(self) -> None:
        events: list[str] = []

        class Inline(Tracked):
            pass

        class Stable(Component):
            value: int = state(0)

            def render(self) -> Text:
                return Text(str(self.value))

        class Root(Component):
            def __init__(self) -> None:
                self.stable = Stable()

            def render(self):
                return (
                    self.boundary(Inline("inline", events), key="inline"),
                    self.boundary(self.stable, key="stable"),
                )

        root = Root()
        runtime = ComponentRuntime(root)
        initial = runtime.render()
        runtime.commit(initial, rendered_revision=runtime.revision)
        original = runtime.components["inline"]

        root.stable.value = 1
        changed = runtime.render(reuse_committed=True)
        runtime.commit(changed, rendered_revision=runtime.revision)

        assert runtime.components["inline"] is not original
        assert events[-2:] == ["unmount:inline", "mount:inline"]


@pytest.mark.parametrize(
    "wrap",
    (
        lambda children: Card(children=children),
        lambda children: Budget(children=children, minimum=0, preferred=100),
        lambda children: Break(children=children),
    ),
)
def test_boundaries_expand_and_namespace_inside_every_primitive_child_container(
    wrap: Callable[[tuple[Node | Boundary, ...]], Node],
) -> None:
    child = Counter("nested")

    class Parent(Component):
        def render(self) -> Node:
            return wrap((self.boundary(child, key="child"),))

    container = render_component_tree(Parent()).nodes[0]
    assert isinstance(container, Card | Budget | Break)
    row = next(node for node in container.children if isinstance(node, Row))
    button = row.items[0]
    assert isinstance(button, Button)
    assert button.key == "child.inc"


def test_component_expansion_preserves_container_metadata() -> None:
    class Parent(Component):
        def render(self) -> Panel:
            return Panel((Text("body"),), accent=0x123456, spoiler=True)

    panel = render_component_tree(Parent()).nodes[0]
    assert isinstance(panel, Panel)
    assert panel.accent == 0x123456
    assert panel.spoiler is True


class Nest(Component):
    """A chain of embeds `depth` deep, each level keyed with a long-ish name."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.child = Nest(depth - 1) if depth else None

    def render(self):
        nodes: list[Node | Boundary] = [Row((Button(label="x", on_click=self._click, key="click"),))]
        if self.child is not None:
            nodes.append(self.boundary(self.child, key=f"level{self.depth}" + "_padding" * 4))
        return [Panel(children=tuple(nodes))]

    async def _click(self, event: PressEvent) -> None: ...


@given(st.integers(min_value=0, max_value=8))
def test_nested_embeds_stay_addressable(depth):
    message_root = MessageRoot(Nest(depth), access=Everyone(), timeout=None)
    view = commit_render(message_root)
    ids = _custom_ids(view)

    assert len(ids) == depth + 1
    assert len(set(ids)) == len(ids), "two controls in one message may not share a custom_id"
    assert all(len(custom_id) <= 100 for custom_id in ids)
    assert len(message_root._handlers) == depth + 1


class PagedChild(Component):
    def render(self):
        return Lines(tuple(f"entry {index}" for index in range(6)), overflow=Paginate(key="items", per=2))


class PagedPair(Component):
    def __init__(self) -> None:
        self.left = PagedChild()
        self.right = PagedChild()

    def render(self):
        return [self.boundary(self.left, key="left"), self.boundary(self.right, key="right")]


def test_embed_namespaces_pager_state_and_controls() -> None:
    message_root = MessageRoot(PagedPair(), access=Everyone(), timeout=None)
    commit_render(message_root)

    assert {key: cursor.position.offset for key, cursor in message_root.presentation.cursors.items()} == {
        "left.items": 0,
        "right.items": 0,
    }
    assert "__cursor_next.left.items" in message_root._handlers
    assert "__cursor_next.right.items" in message_root._handlers


def test_duplicate_sibling_embed_keys_are_rejected() -> None:
    class Duplicate(Component):
        def render(self):
            return [self.boundary(Counter("one"), key="same"), self.boundary(Counter("two"), key="same")]

    with pytest.raises(LayoutInvariantError, match="duplicate Boundary key"):
        commit_render(MessageRoot(Duplicate(), access=Everyone(), timeout=None))


def test_one_component_instance_cannot_occupy_two_paths() -> None:
    child = Counter("shared")

    class Duplicate(Component):
        def render(self):
            return [self.boundary(child, key="one"), self.boundary(child, key="two")]

    with pytest.raises(LayoutInvariantError, match="already embedded"):
        commit_render(MessageRoot(Duplicate(), access=Everyone(), timeout=None))


def test_component_embedding_cycles_are_rejected() -> None:
    class Cycle(Component):
        def render(self):
            return self.boundary(self, key="self")

    with pytest.raises(LayoutInvariantError, match="embedding cycle"):
        commit_render(MessageRoot(Cycle(), access=Everyone(), timeout=None))


class Tracked(Component):
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def render(self) -> Node:
        return Text(self.name)

    def on_mount(self) -> None:
        self.events.append(f"mount:{self.name}")

    def on_unmount(self) -> None:
        self.events.append(f"unmount:{self.name}")


async def test_keyed_component_lifecycle_tracks_replacement_and_finish() -> None:
    events: list[str] = []

    class Parent(Tracked):
        def __init__(self) -> None:
            super().__init__("parent", events)
            self.child = Tracked("first", events)

        def render(self):
            return self.boundary(self.child, key="child")

    parent = Parent()
    message_root = MessageRoot(parent, access=Everyone(), timeout=None)
    commit_render(message_root)
    assert events == ["mount:parent", "mount:first"]

    parent.child = Tracked("second", events)
    message_root.invalidate()
    commit_render(message_root)
    assert events[-2:] == ["unmount:first", "mount:second"]

    await message_root.finish(disable=False)
    assert events[-2:] == ["unmount:second", "unmount:parent"]


def test_typed_context_flows_to_descendants_without_entering_component_state() -> None:
    greeting = ContextKey[str]("greeting")

    class Child(Component):
        def render(self) -> Node:
            return Text(self.inject(greeting))

    class Parent(Component):
        def __init__(self) -> None:
            self.child = Child()

        def render(self):
            self.provide(greeting, "hello from context")
            return self.boundary(self.child, key="child")

    view = commit_render(MessageRoot(Parent(), access=Everyone(), timeout=None))

    assert "hello from context" in _texts(view)


def test_semantic_actions_are_namespaced_across_embedded_instances() -> None:
    async def run(_event) -> None: ...

    class Child(Component):
        def render(self):
            return ActionControls((ActionControl("run", "Run", run),), key="toolbar")

    class Parent(Component):
        def __init__(self) -> None:
            self.left = Child()
            self.right = Child()

        def render(self):
            return (self.boundary(self.left, key="left"), self.boundary(self.right, key="right"))

    message_root = MessageRoot(Parent(), access=Everyone(), timeout=None)
    commit_render(message_root)

    assert {"left.run", "right.run"} <= message_root._handlers.keys()


def test_all_keyed_semantics_are_namespaced_through_semantic_containers() -> None:
    async def change(_event) -> None: ...

    class Child(Component):
        def render(self):
            return Group(
                (
                    List(
                        tuple(ListItem(str(index), f"Item {index}") for index in range(6)),
                        key="entries",
                        page_size=2,
                    ),
                    Choices(
                        "choice",
                        tuple(Choice(str(index), f"Choice {index}") for index in range(6)),
                        Controlled((), change),
                    ),
                )
            )

    class Parent(Component):
        def __init__(self) -> None:
            self.left = Child()
            self.right = Child()

        def render(self):
            return (self.boundary(self.left, key="left"), self.boundary(self.right, key="right"))

    message_root = MessageRoot(Parent(), access=Everyone(), timeout=None)
    commit_render(message_root)

    assert {"left.entries", "right.entries"} <= message_root.presentation.cursors.keys()
    assert {"left.choice", "right.choice"} <= message_root._handlers.keys()
    assert "__cursor_next.left.entries" in message_root._handlers
    assert "__cursor_next.right.entries" in message_root._handlers


class TestRenderItem:
    """One node, drawn to an item a host places into a view it assembles itself."""

    def test_the_item_is_detached_from_the_view_the_renderer_built(self) -> None:
        item = sd.render_item(sl.heading("Title"))

        assert isinstance(item, discord.ui.TextDisplay)
        assert item._view is None
        assert item._parent is None

    def test_the_surrounding_view_is_the_hosts_to_build(self) -> None:
        """Nothing half-built survives the call: the renderer's view is discarded here."""
        host = discord.ui.LayoutView(timeout=None)

        host.add_item(sd.render_item(sl.section(sl.heading("Title"), sl.paragraph("Body"))))

        assert len(host.children) == 1
        assert host.children[0]._view is host

    def test_a_node_that_draws_nothing_is_refused_rather_than_indexed(self) -> None:
        with pytest.raises(sd.MessageModeError, match="produced no item"):
            sd.render_item(sl.group())
