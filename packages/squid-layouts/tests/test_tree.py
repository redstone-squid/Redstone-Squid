"""The authored-tree structure has one fail-closed traversal definition."""

from dataclasses import dataclass

import pytest

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.primitives import Text
from squid_layouts.runtime._tree import map_layout_children
from squid_layouts.semantic import LayoutNode


@dataclass(frozen=True, slots=True)
class UnregisteredContainer:
    children: tuple[LayoutNode, ...]


def test_a_new_structural_node_must_register_its_children() -> None:
    node = UnregisteredContainer((Text("hidden"),))

    with pytest.raises(LayoutInvariantError, match="UnregisteredContainer has unregistered layout fields: children"):
        map_layout_children(node, "$.0", lambda child, _path: (child,))  # type: ignore[arg-type]
