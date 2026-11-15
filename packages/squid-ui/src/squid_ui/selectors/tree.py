"""Occurrence snapshots for authored UI and explicitly adapted trees."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from squid_ui import primitives as p
from squid_ui import semantic as s
from squid_ui.primitives.nodes import Boundary
from squid_ui.runtime.definition import Component
from squid_ui.runtime.tree import ComponentTree
from squid_ui.selectors._scene import is_scene_node, scene_children
from squid_ui.target_types import Renderable

# Query edges include controls and content values that layout rewriting treats as leaves.
_UI_FIELDS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (s.Group, ("children",)),
    (s.Stack, ("children",)),
    (s.Cluster, ("children",)),
    (s.Themed, ("children",)),
    (s.Block, ("children",)),
    (s.Section, ("heading", "children")),
    (s.Article, ("heading", "children")),
    (s.Aside, ("children",)),
    (s.Truncated, ("node",)),
    (s.Spilled, ("node",)),
    (s.OptionalContent, ("node",)),
    (s.BestEffort, ("node",)),
    (s.Budgeted, ("node",)),
    (s.Unbreakable, ("node",)),
    (s.KeepWithNext, ("node",)),
    (s.Paged, ("node",)),
    (s.FallbackContent, ("primary", "alternates")),
    (p.Panel, ("children",)),
    (p.Budget, ("children",)),
    (p.Break, ("children",)),
    (p.Card, ("children",)),
    (p.Extension, ("fallback",)),
    (p.Variants, ("variants",)),
    (p.Variant, ("nodes",)),
    (s.ActionControls, ("items",)),
    (s.ControlGroup, ("controls",)),
    (s.Items, ("items",)),
    (s.Item, ("label", "children")),
    (s.Choices, ("choices",)),
    (s.Entities, ("choices",)),
    (s.RoutedChoices, ("choices",)),
    (s.Navigation, ("options",)),
    (s.List, ("items",)),
    (s.Fields, ("fields",)),
    (s.Table, ("columns", "rows")),
    (s.Columns, ("columns",)),
    (s.Region, ("header", "children", "footer")),
    (s.Media, ("items",)),
    (s.Grid, ("cells",)),
    (s.Figure, ("media",)),
    (s.Details, ("summary", "children")),
    (p.Row, ("items",)),
    (p.ControlGroup, ("items",)),
    (p.Section, ("texts", "accessory")),
    (p.SelectMenu, ("options",)),
    (p.RoutedSelect, ("options",)),
    (p.Gallery, ("items",)),
    (p.MediaCollection, ("items",)),
)

type Children = Callable[[object], Iterable[tuple[str, object]]]
"""An adapter yields named child edges in display order; it must not render or mutate nodes."""


def ui_children(node: object) -> Iterable[tuple[str, object]]:
    """Declared UI edges, including controls and all authored alternatives.

    Application records, callbacks, and state ownership objects are leaves. Custom
    trees can supply their own adapter to snapshot instead of exposing arbitrary fields.
    """
    if is_scene_node(node):
        yield from scene_children(node)
        return
    for kind, names in _UI_FIELDS:
        if isinstance(node, kind):
            for name in names:
                value = getattr(node, name)
                if isinstance(value, tuple | list):
                    yield from ((f"{name}.{index}", item) for index, item in enumerate(value))
                elif value is not None:
                    yield name, value
            return
    if isinstance(node, Renderable) and is_dataclass(node):
        structural = {"children", "node", "primary", "alternates", "fallback", "variants"}
        if structural.intersection(entry.name for entry in fields(node)):
            msg = f"{type(node).__name__} has unregistered query edges; supply a children adapter"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Match[T]:
    """One occurrence. Paths address this snapshot, not identity across future renders.

    Nodes are original references, not deep copies; component state may subsequently change.
    owner identifies the producing component placement, and key is the authored local key.
    """

    node: T
    path: str
    owner: str | None
    key: str | None
    qualified_key: str | None
    _index: int
    _token: object

    def precedes(self, other: Match[Any]) -> bool:
        """Whether this occurrence precedes another in the same snapshot's depth-first order.

        Ancestors precede their descendants. This does not imply a sibling relationship.
        Comparing different captures raises ValueError, even for the same underlying tree.
        """
        if self._token is not other._token:
            message = "ordering requires matches from the same snapshot"
            raise ValueError(message)
        return self._index < other._index


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A fixed topology in depth-first order. Querying it never invokes render or lifecycle hooks."""

    matches: tuple[Match[object], ...]
    parents: tuple[int | None, ...]
    ends: tuple[int, ...]
    _token: object

    def index(self, match: Match[Any]) -> int:
        if match._token is not self._token:
            msg = "scope belongs to a different snapshot"
            raise ValueError(msg)
        return match._index


def snapshot(tree: object, *, children: Children = ui_children) -> Snapshot:
    """Capture an authored ComponentTree, planned scene, or existing UI values.

    ComponentTree preserves placements and local keys. Plain expanded nodes have only
    their existing keys and no owner metadata. Lists and tuples are transparent roots.
    Repeated references become separate occurrences; ancestor cycles raise ValueError.
    A custom children adapter also permits querying frontend or application-specific trees.
    """
    if isinstance(tree, Snapshot):
        if children is not ui_children:
            message = "children cannot change traversal of an existing snapshot"
            raise ValueError(message)
        return tree
    rendered = tree if isinstance(tree, ComponentTree) else None
    if rendered is not None:
        if rendered.suspended or rendered.deferred:
            msg = "cannot inspect an incomplete component render"
            raise ValueError(msg)
        if "$" not in rendered.authored_nodes or "$" not in rendered.components:
            msg = "ComponentTree has no authored snapshot; inspect its nodes explicitly"
            raise ValueError(msg)
        roots: tuple[object, ...] = (rendered.components["$"],)
    else:
        roots = tuple(tree) if isinstance(tree, tuple | list) else (tree,)
    token = object()
    matches: list[Match[object]] = []
    parents: list[int | None] = []
    ends: list[int] = []
    active: set[int] = set()

    def visit(node: object, path: str, parent: int | None, owner: str | None, placement: str | None = None) -> None:
        local_key = getattr(node, "key", None)
        if isinstance(node, Boundary):
            placement = node.key if owner in (None, "$") else f"{owner}.{node.key}"
            local_key = node.key
            node = node.component
        elif isinstance(node, Component) and placement is None:
            local_key = node._composition_key
            placement = local_key if owner in (None, "$") else f"{owner}.{local_key}"
        if id(node) in active:
            msg = f"cycle in query tree at {path}"
            raise ValueError(msg)
        active.add(id(node))
        try:
            index = len(matches)
            key = local_key if isinstance(local_key, str) else None
            qualified = key if owner in (None, "$") or key is None else f"{owner}.{key}"
            matches.append(Match(node, path, owner, key, placement or qualified, index, token))
            parents.append(parent)
            ends.append(0)
            if isinstance(node, Component):
                if rendered is None or placement not in rendered.authored_nodes:
                    msg = f"{path}: component requires an authored ComponentTree"
                    raise ValueError(msg)
                edges = ((str(i), value) for i, value in enumerate(rendered.authored_nodes[placement]))
                owner = placement
            else:
                edges = children(node)
            for edge, child in edges:
                visit(child, f"{path}/{edge}", index, owner)
            ends[index] = len(matches)
        finally:
            active.remove(id(node))

    for index, root in enumerate(roots):
        visit(root, f"$/{index}", None, None, "$" if rendered is not None else None)
    return Snapshot(tuple(matches), tuple(parents), tuple(ends), token)
