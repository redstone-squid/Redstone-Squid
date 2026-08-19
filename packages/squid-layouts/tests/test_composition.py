"""Composable components: embedding, key namespacing, and where invalidation travels."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    Component,
    ContextKey,
    LayoutInvariantError,
    PressEvent,
    state,
)
from squid_layouts.discord import Mount
from squid_layouts.discord.testing import fake_interaction
from squid_layouts.primitives import (
    Button,
    Embed,
    Heading,
    Lines,
    Node,
    Paginate,
    Panel,
    Row,
    Text,
)
from squid_layouts.semantic import Action, Actions, Choice, Choices, Group, List, ListItem


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
        return [Heading("Pair"), self.embed(self.left, key="left"), self.embed(self.right, key="right")]


def _custom_ids(view: discord.ui.LayoutView) -> list[str]:
    return [item.custom_id or "" for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class TestEmbedding:
    async def test_each_instance_answers_only_its_own_control(self):
        pair = Pair()
        mount = Mount(pair, timeout=None)
        mount.build_view()

        await mount.dispatch("left.inc", fake_interaction())

        assert (pair.left.count, pair.right.count) == (1, 0)

        await mount.dispatch("right.inc", fake_interaction())

        assert (pair.left.count, pair.right.count) == (1, 1)

    def test_controls_are_namespaced_including_every_explicit_key(self):
        mount = Mount(Pair(), timeout=None)
        mount.build_view()
        assert set(mount._handlers) == {"left.inc", "left.help", "right.inc", "right.help"}

    def test_a_childs_state_change_re_renders_the_root_message(self):
        pair = Pair()
        mount = Mount(pair, timeout=None)
        mount.build_view()

        pair.right.count = 3

        assert mount._dirty
        assert "right: 3" in _texts(mount.build_view())

    def test_components_do_not_expose_the_frontend_mount(self):
        pair = Pair()
        Mount(pair, timeout=None).build_view()
        assert not hasattr(pair.left, "mount")

    def test_embedding_does_not_mutate_the_childs_own_keys(self):
        # render() stays pure: namespacing rewrites the returned tree, not the component.
        pair = Pair()
        Mount(pair, timeout=None).build_view()
        row = pair.left.render()[1]
        assert isinstance(row, Row)
        button = row.items[0]
        assert isinstance(button, Button)
        assert button.key == "inc"


class Nest(Component):
    """A chain of embeds `depth` deep, each level keyed with a long-ish name."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.child = Nest(depth - 1) if depth else None

    def render(self):
        nodes: list[Node | Embed] = [Row((Button(label="x", on_click=self._click, key="click"),))]
        if self.child is not None:
            nodes.append(self.embed(self.child, key=f"level{self.depth}" + "_padding" * 4))
        return [Panel(children=tuple(nodes))]

    async def _click(self, event: PressEvent) -> None: ...


@given(st.integers(min_value=0, max_value=8))
def test_nested_embeds_stay_addressable(depth):
    mount = Mount(Nest(depth), timeout=None)
    view = mount.build_view()
    ids = _custom_ids(view)

    assert len(ids) == depth + 1
    assert len(set(ids)) == len(ids), "two controls in one message may not share a custom_id"
    assert all(len(custom_id) <= 100 for custom_id in ids)
    assert len(mount._handlers) == depth + 1


class PagedChild(Component):
    def render(self):
        return Lines(tuple(f"entry {index}" for index in range(6)), overflow=Paginate(key="items", per=2))


class PagedPair(Component):
    def __init__(self) -> None:
        self.left = PagedChild()
        self.right = PagedChild()

    def render(self):
        return [self.embed(self.left, key="left"), self.embed(self.right, key="right")]


def test_embed_namespaces_pager_state_and_controls() -> None:
    mount = Mount(PagedPair(), timeout=None)
    mount.build_view()

    assert {key: cursor.index for key, cursor in mount.presentation.cursors.items()} == {
        "left.items": 0,
        "right.items": 0,
    }
    assert "__page_next.left.items" in mount._handlers
    assert "__page_next.right.items" in mount._handlers


def test_duplicate_sibling_embed_keys_are_rejected() -> None:
    class Duplicate(Component):
        def render(self):
            return [self.embed(Counter("one"), key="same"), self.embed(Counter("two"), key="same")]

    with pytest.raises(LayoutInvariantError, match="duplicate Embed key"):
        Mount(Duplicate(), timeout=None).build_view()


def test_one_component_instance_cannot_occupy_two_paths() -> None:
    child = Counter("shared")

    class Duplicate(Component):
        def render(self):
            return [self.embed(child, key="one"), self.embed(child, key="two")]

    with pytest.raises(LayoutInvariantError, match="already embedded"):
        Mount(Duplicate(), timeout=None).build_view()


def test_component_embedding_cycles_are_rejected() -> None:
    class Cycle(Component):
        def render(self):
            return self.embed(self, key="self")

    with pytest.raises(LayoutInvariantError, match="embedding cycle"):
        Mount(Cycle(), timeout=None).build_view()


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
            return self.embed(self.child, key="child")

    parent = Parent()
    mount = Mount(parent, timeout=None)
    mount.build_view()
    assert events == ["mount:parent", "mount:first"]

    parent.child = Tracked("second", events)
    mount.invalidate()
    mount.build_view()
    assert events[-2:] == ["unmount:first", "mount:second"]

    await mount.finish(disable=False)
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
            return self.embed(self.child, key="child")

    view = Mount(Parent(), timeout=None).build_view()

    assert "hello from context" in _texts(view)


def test_semantic_actions_are_namespaced_across_embedded_instances() -> None:
    async def run(_event) -> None: ...

    class Child(Component):
        def render(self):
            return Actions((Action("run", "Run", run),), key="toolbar")

    class Parent(Component):
        def __init__(self) -> None:
            self.left = Child()
            self.right = Child()

        def render(self):
            return (self.embed(self.left, key="left"), self.embed(self.right, key="right"))

    mount = Mount(Parent(), timeout=None)
    mount.build_view()

    assert {"left.run", "right.run"} <= mount._handlers.keys()


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
                        (),
                        change,
                    ),
                )
            )

    class Parent(Component):
        def __init__(self) -> None:
            self.left = Child()
            self.right = Child()

        def render(self):
            return (self.embed(self.left, key="left"), self.embed(self.right, key="right"))

    mount = Mount(Parent(), timeout=None)
    mount.build_view()

    assert {"left.entries", "right.entries"} <= mount.presentation.cursors.keys()
    assert {"left.choice", "right.choice"} <= mount._handlers.keys()
    assert "__page_next.left.entries" in mount._handlers
    assert "__page_next.right.entries" in mount._handlers
