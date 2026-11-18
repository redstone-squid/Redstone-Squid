"""Occurrence indexes for authored UI and explicitly adapted trees."""

from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any

from squid_ui._query_index import QueryIndexOwner
from squid_ui.authoring import semantic as s
from squid_ui.boundary import Boundary
from squid_ui.native import NativeTraits, NativeValue
from squid_ui.runtime.definition import Component
from squid_ui.runtime.tree import ComponentTree
from squid_ui.selectors._scene import is_scene_node, scene_children
from squid_ui.target_types import Renderable

# Query edges include controls and content values that layout rewriting treats as leaves.
_UI_FIELDS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (s.Contextual, ("children", "_prepared")),
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
)

type Children = Callable[[object], Iterable[tuple[str, object]]]
"""An adapter yields named child edges in display order; it must not render or mutate nodes."""


def ui_children(node: object) -> Iterable[tuple[str, object]]:
    """Declared UI edges, including controls and all authored alternatives.

    Application records, callbacks, and state ownership objects are leaves. Custom
    trees can supply their own adapter to index_tree instead of exposing arbitrary fields.
    """
    if is_scene_node(node):
        yield from scene_children(node)
        return
    fields_to_visit = (
        ((type(node), NativeTraits.of(node).query_fields),) if isinstance(node, NativeValue) else _UI_FIELDS
    )
    for kind, names in fields_to_visit:
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
    """One occurrence. Paths address this tree index, not identity across future renders.

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
        """Whether this occurrence precedes another in the same tree index's depth-first order.

        Ancestors precede their descendants. This does not imply a sibling relationship.
        Comparing different captures raises ValueError, even for the same underlying tree.
        """
        if self._token is not other._token:
            message = "ordering requires matches from the same tree index"
            raise ValueError(message)
        return self._index < other._index


@dataclass(frozen=True, slots=True)
class TreeIndex:
    """A fixed topology in depth-first order. Querying it never invokes render or lifecycle hooks."""

    matches: tuple[Match[object], ...]
    parents: tuple[int | None, ...]
    ends: tuple[int, ...]
    _token: object
    _types: Mapping[type, tuple[int, ...]]
    _keys: Mapping[tuple[str, str], tuple[int, ...]]

    def candidates(self, kind: type | None, key: tuple[str, str] | None, start: int, end: int) -> Iterable[int]:
        """Narrow to a stable type or captured key, preserving occurrence order."""
        choices: list[tuple[int, ...]] = []
        if kind is not None:
            choices.append(self._types.get(kind, ()))
        if key is not None:
            choices.append(self._keys.get(key, ()))
        if not choices:
            return range(start, end)
        indices = min(choices, key=len)
        return indices[bisect_left(indices, start) : bisect_left(indices, end)]

    def index(self, match: Match[Any]) -> int:
        if match._token is not self._token:
            msg = "scope belongs to a different tree index"
            raise ValueError(msg)
        return match._index

    @classmethod
    def capture(
        cls,
        matches: tuple[Match[object], ...],
        parents: tuple[int | None, ...],
        ends: tuple[int, ...],
        token: object,
    ) -> TreeIndex:
        """Index an adapter's depth-first occurrences and subtree boundaries."""
        by_type: dict[type, list[int]] = {}
        by_key: dict[tuple[str, str], list[int]] = {}
        for match in matches:
            for kind in type(match.node).__mro__:
                # Custom instance checks (including virtual ABC registrations) remain predicates.
                if type(kind) is type:
                    by_type.setdefault(kind, []).append(match._index)
            for name, value in (("key", match.key), ("qualified_key", match.qualified_key)):
                if value is not None:
                    by_key.setdefault((name, value), []).append(match._index)
        return cls(
            matches,
            parents,
            ends,
            token,
            MappingProxyType({kind: tuple(indices) for kind, indices in by_type.items()}),
            MappingProxyType({key: tuple(indices) for key, indices in by_key.items()}),
        )


_INDEX_LOCK = RLock()


def index_tree(tree: object, *, children: Children = ui_children) -> TreeIndex:
    """Index existing UI without rendering, reusing the index owned by each render.

    ComponentTree and Scene retain a lazy index for their fixed topology with the default
    adapter. Raw nodes, lists, native views, and custom adapters are indexed afresh.
    Reuse an explicit TreeIndex for shared scopes on those mutable representations.
    Values are references, not copies; predicates are evaluated again on each query.
    """
    if isinstance(tree, TreeIndex):
        if children is not ui_children:
            message = "children cannot change traversal of an existing tree index"
            raise ValueError(message)
        return tree
    if isinstance(tree, QueryIndexOwner) and children is ui_children:
        # A render may be inspected from different threads. Publish one occurrence identity.
        with _INDEX_LOCK:
            cached = getattr(tree, "_query_index", None)
            if isinstance(cached, tuple) and cached[0] == id(tree) and isinstance(cached[1], TreeIndex):
                return cached[1]
            indexed = _build_index(tree, children=children)
            object.__setattr__(tree, "_query_index", (id(tree), indexed))
            return indexed
    return _build_index(tree, children=children)


def _build_index(tree: object, *, children: Children) -> TreeIndex:
    rendered = tree if isinstance(tree, ComponentTree) else None
    if rendered is not None:
        if rendered.suspended or rendered.deferred:
            msg = "cannot inspect an incomplete component render"
            raise ValueError(msg)
        if "$" not in rendered.authored_nodes or "$" not in rendered.components:
            msg = "ComponentTree has no authored render data; inspect its nodes explicitly"
            raise ValueError(msg)
        roots: tuple[object, ...] = (rendered.components["$"],)
    else:
        roots = tuple(tree) if isinstance(tree, tuple | list) else (tree,)
    token = object()
    matches: list[Match[object]] = []
    parents: list[int | None] = []
    ends: list[int] = []
    active: set[int] = set()
    placements: set[str] = set()

    def visit(node: object, path: str, parent: int | None, owner: str | None, placement: str | None = None) -> None:
        local_key = getattr(node, "key", None)
        if isinstance(node, s.ComponentPlacement) and rendered is not None and rendered.query_nodes is not None:
            placement = node.placement
            if placement in placements:
                return
            placements.add(placement)
            local_key = placement.rsplit(".", 1)[-1]
            node = rendered.components[placement]
        elif isinstance(node, Boundary):
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
            if (
                rendered is not None
                and rendered.query_nodes is not None
                and key is not None
                and owner not in (None, "$")
                and key.startswith(f"{owner}.")
            ):
                key = key[len(owner) + 1 :]
            qualified = key if owner in (None, "$") or key is None else f"{owner}.{key}"
            matches.append(Match(node, path, owner, key, placement or qualified, index, token))
            parents.append(parent)
            ends.append(0)
            if isinstance(node, Component):
                if rendered is None or placement is None or placement not in rendered.authored_nodes:
                    msg = f"{path}: component requires an authored ComponentTree"
                    raise ValueError(msg)
                nodes = (
                    rendered.query_nodes.get(placement, rendered.authored_nodes[placement])
                    if rendered.query_nodes is not None
                    else rendered.authored_nodes[placement]
                )
                edges = ((str(i), value) for i, value in enumerate(nodes))
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
    return TreeIndex.capture(tuple(matches), tuple(parents), tuple(ends), token)
