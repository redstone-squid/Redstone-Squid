"""Every UI class declares the fields that hold other UI values, and traversal follows them."""

import re
from annotationlib import Format, get_annotations
from dataclasses import dataclass, fields, is_dataclass
from types import ModuleType

import pytest

from squid_ui.authoring import presentations, semantic
from squid_ui.authoring.semantic import LayoutNode
from squid_ui.authoring.traversal import map_layout_children
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.primitives import Text
from squid_ui.ext.discord.primitives import nodes as discord_nodes
from squid_ui.ext.discord.target_types import DiscordTarget
from squid_ui.target_types import Renderable, Structure

_NODE_ANNOTATION = re.compile(r"\b(LayoutNode|Renderable|Node)\b")


@dataclass(frozen=True, slots=True)
class UnregisteredContainer(Renderable[DiscordTarget]):
    children: tuple[LayoutNode[DiscordTarget], ...]


def _ui_classes(*modules: ModuleType) -> list[type]:
    return [
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__ and is_dataclass(value)
    ]


def test_a_new_structural_node_must_declare_its_children() -> None:
    node = UnregisteredContainer((Text("hidden"),))

    with pytest.raises(LayoutInvariantError, match="UnregisteredContainer has unregistered layout fields: children"):
        map_layout_children(node, "$.0", lambda child, _path: (child,))


@pytest.mark.parametrize(
    "kind", _ui_classes(semantic, presentations, discord_nodes), ids=lambda kind: f"{kind.__module__}.{kind.__name__}"
)
def test_every_node_field_is_declared_in_structure(kind: type) -> None:
    structure: Structure = getattr(kind, "STRUCTURE", Structure())
    declared = {*structure.layout, *(field for field, _ in structure.nested), *structure.query_fields}
    names = {field.name for field in fields(kind)}
    assert declared <= names, f"{kind.__name__} declares fields it does not have: {sorted(declared - names)}"
    annotations = {
        name: annotation
        for base in reversed(kind.__mro__)
        for name, annotation in get_annotations(base, format=Format.STRING).items()
    }
    holding_nodes = {name for name in names if _NODE_ANNOTATION.search(annotations[name])}
    # Contextual components are boundaries the runtime expands into `_prepared`.
    holding_nodes.discard("components")
    assert holding_nodes <= declared, f"{kind.__name__} holds undeclared nodes in {sorted(holding_nodes - declared)}"
