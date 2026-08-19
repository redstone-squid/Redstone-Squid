"""Composable components: embedding, key namespacing, and where invalidation travels."""

import discord
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import Button, Component, Heading, Mount, Node, Panel, Row, Text, state
from squid_layouts.testing import fake_interaction


class Counter(Component):
    count: int = state(0)

    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return [
            Text(f"{self.name}: {self.count}"),
            Row((Button(label="+1", on_click=self.increment, key="inc"), Button(label="?", on_click=self.noop))),
        ]

    async def increment(self, interaction: discord.Interaction) -> None:
        self.count += 1

    async def noop(self, interaction: discord.Interaction) -> None: ...


class Pair(Component):
    """The shape that used to cross-wire: two instances of one child class."""

    def __init__(self) -> None:
        self.left = Counter("left")
        self.right = Counter("right")

    def render(self):
        return [Heading("Pair"), *self.embed(self.left, key="left"), *self.embed(self.right, key="right")]


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

    def test_controls_are_namespaced_including_the_keyless_ones(self):
        mount = Mount(Pair(), timeout=None)
        mount.build_view()
        assert set(mount._handlers) == {"left.inc", "left.auto0", "right.inc", "right.auto0"}

    def test_a_childs_state_change_re_renders_the_root_message(self):
        pair = Pair()
        mount = Mount(pair, timeout=None)
        mount.build_view()

        pair.right.count = 3

        assert mount._dirty
        assert "right: 3" in _texts(mount.build_view())

    def test_a_child_reaches_the_mount_through_its_parent(self):
        pair = Pair()
        mount = Mount(pair, timeout=None)
        mount.build_view()
        assert pair.left.mount is mount

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
        nodes: list[Node] = [Row((Button(label="x", on_click=self._click),))]
        if self.child is not None:
            nodes.extend(self.embed(self.child, key=f"level{self.depth}" + "_padding" * 4))
        return [Panel(children=tuple(nodes))]

    async def _click(self, interaction: discord.Interaction) -> None: ...


@given(st.integers(min_value=0, max_value=8))
def test_nested_embeds_stay_addressable(depth):
    mount = Mount(Nest(depth), timeout=None)
    view = mount.build_view()
    ids = _custom_ids(view)

    assert len(ids) == depth + 1
    assert len(set(ids)) == len(ids), "two controls in one message may not share a custom_id"
    assert all(len(custom_id) <= 100 for custom_id in ids)
    assert len(mount._handlers) == depth + 1
