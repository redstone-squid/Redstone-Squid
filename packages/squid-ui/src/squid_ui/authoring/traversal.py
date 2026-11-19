"""Structural traversal shared by component expansion and contextual planning."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import cast

from squid_ui.authoring.semantic import (
    ActionControl as SemanticActionControl,
)
from squid_ui.authoring.semantic import (
    ActionControls as SemanticActionControls,
)
from squid_ui.authoring.semantic import (
    AnyLayoutNode,
    Contextual,
    LayoutNode,
    PreparedSlot,
    Region,
)
from squid_ui.authoring.semantic import (
    Choices as SemanticChoices,
)
from squid_ui.authoring.semantic import ControlGroup as SemanticControlGroup
from squid_ui.authoring.semantic import (
    Details as SemanticDetails,
)
from squid_ui.authoring.semantic import Download as SemanticDownload
from squid_ui.authoring.semantic import (
    FormTrigger as SemanticFormTrigger,
)
from squid_ui.authoring.semantic import Grid as SemanticGrid
from squid_ui.authoring.semantic import (
    Items as SemanticItems,
)
from squid_ui.authoring.semantic import (
    Link as SemanticLink,
)
from squid_ui.authoring.semantic import (
    List as SemanticList,
)
from squid_ui.authoring.semantic import (
    Media as SemanticMedia,
)
from squid_ui.authoring.semantic import (
    Navigation as SemanticNavigation,
)
from squid_ui.authoring.semantic import Paged as SemanticPaged
from squid_ui.authoring.semantic import Roster as SemanticRoster
from squid_ui.authoring.semantic import RoutedActionControl as SemanticRoutedActionControl
from squid_ui.authoring.semantic import (
    Table as SemanticTable,
)
from squid_ui.authoring.semantic import Toggle as SemanticToggle
from squid_ui.errors import LayoutInvariantError
from squid_ui.native import NativeValue
from squid_ui.target_types import Structure

type LayoutTransform[RenderTargetT, ContextT = object] = Callable[
    [LayoutNode[RenderTargetT, ContextT], str], Sequence[LayoutNode[RenderTargetT, ContextT]]
]
type _LayoutRoute = tuple[_FieldStep | _IndexStep | _SequenceStep, ...]
type _RoutedLayoutTransform[RenderTargetT, ContextT = object] = Callable[
    [LayoutNode[RenderTargetT, ContextT], str, _LayoutRoute], Sequence[LayoutNode[RenderTargetT, ContextT]]
]


@dataclass(frozen=True, slots=True)
class _FieldStep:
    field: str


@dataclass(frozen=True, slots=True)
class _IndexStep:
    field: str | None
    index: int


@dataclass(frozen=True, slots=True)
class _SequenceStep:
    field: str | None
    start: int


def map_layout_children[RenderTargetT, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT], path: str, transform: LayoutTransform[RenderTargetT, ContextT]
) -> LayoutNode[RenderTargetT, ContextT]:
    """Rebuild ``node`` after transforming each layout child its class declares in `Structure`.

    Sequence positions accept a transform that splices zero or more nodes. Singular positions
    require exactly one result. ``path`` is diagnostic only and is extended consistently for
    every structural shape.
    """

    return _map_layout_children_routed(node, path, (), lambda child, child_path, _route: transform(child, child_path))


def _map_layout_children_routed[RenderTargetT, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
    path: str,
    route: _LayoutRoute,
    transform: _RoutedLayoutTransform[RenderTargetT, ContextT],
) -> LayoutNode[RenderTargetT, ContextT]:
    """Rebuild a node while identifying where each transformed child lands."""

    structure = Structure.of(node)
    updates: dict[str, object] = {}
    for field in structure.layout:
        value = getattr(node, field)
        if value is None:
            continue
        if isinstance(value, tuple):
            # Children positions join the path directly; other slots are named.
            prefix = path if field == "children" else f"{path}.{field}"
            updates[field] = _map_many_at(value, prefix, route, field, transform)
        else:
            updates[field] = _map_one(value, f"{path}.{field}", (*route, _FieldStep(field)), transform)
    for field, child in structure.nested:
        records = []
        for index, record in enumerate(getattr(node, field)):
            record_path = f"{path}.{field}.{index}"
            record_route = (*route, _IndexStep(field, index))
            value = getattr(record, child)
            mapped = (
                _map_many_at(value, record_path, record_route, child, transform)
                if isinstance(value, tuple)
                else _map_one(value, record_path, (*record_route, _FieldStep(child)), transform)
            )
            records.append(replace(record, **{child: mapped}))
        updates[field] = tuple(records)
    return cast(LayoutNode[RenderTargetT, ContextT], replace(node, **updates)) if updates else node  # type: ignore[arg-type]


def _map_one[RenderTargetT, ContextT = object](
    child: LayoutNode[RenderTargetT, ContextT],
    path: str,
    route: _LayoutRoute,
    transform: _RoutedLayoutTransform[RenderTargetT, ContextT],
) -> LayoutNode[RenderTargetT, ContextT]:
    transformed = tuple(transform(child, path, route))
    if len(transformed) != 1:
        message = f"{path}: this structural position requires exactly one node"
        raise LayoutInvariantError(message)
    return transformed[0]


def _map_many_at[RenderTargetT, ContextT = object](
    children: Sequence[LayoutNode[RenderTargetT, ContextT]],
    path: str,
    route: _LayoutRoute,
    field: str,
    transform: _RoutedLayoutTransform[RenderTargetT, ContextT],
) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
    transformed: list[LayoutNode[RenderTargetT, ContextT]] = []
    for index, child in enumerate(children):
        transformed.extend(transform(child, f"{path}.{index}", (*route, _SequenceStep(field, len(transformed)))))
    return tuple(transformed)


type _SemanticActionItem = (
    SemanticActionControl
    | SemanticLink
    | SemanticRoutedActionControl
    | SemanticToggle
    | SemanticFormTrigger
    | SemanticControlGroup
)


def _namespace[RenderTargetT, ContextT = object](
    nodes: Sequence[LayoutNode[RenderTargetT, ContextT]], prefix: str
) -> list[LayoutNode[RenderTargetT, ContextT]]:
    """Rewrite an embedded subtree's control keys under ``prefix``.

    Explicit control keys are scoped under the embed path, so inserting a sibling cannot
    change the identity of any existing action.
    """

    def rewrite_semantic_control(
        item: SemanticActionControl | SemanticLink | SemanticRoutedActionControl,
    ) -> SemanticActionControl | SemanticLink | SemanticRoutedActionControl:
        return replace(item, key=f"{prefix}.{item.key}")

    def rewrite_semantic_action(item: _SemanticActionItem) -> _SemanticActionItem:
        if isinstance(item, SemanticControlGroup):
            return replace(
                item,
                key=f"{prefix}.{item.key}",
                controls=tuple(rewrite_semantic_control(control) for control in item.controls),
            )
        return replace(item, key=f"{prefix}.{item.key}")

    def rewrite(node: AnyLayoutNode) -> AnyLayoutNode:
        if isinstance(node, PreparedSlot):
            return node
        if isinstance(node, NativeValue):
            node = cast(AnyLayoutNode, node.semantics.namespace(node, prefix))
        match node:
            case SemanticActionControls(items=items, key=key):
                return replace(
                    node,
                    key=f"{prefix}.{key}",
                    items=tuple(rewrite_semantic_action(item) for item in items),
                )
            case Contextual(key=key):
                keyed = replace(node, key=f"{prefix}.{key}")
                return map_layout_children(keyed, "$", lambda child, _path: (rewrite(child),))
            case SemanticDetails(key=key) | SemanticItems(key=key) | Region(key=key):
                keyed = replace(node, key=f"{prefix}.{key}")
                return map_layout_children(keyed, "$", lambda child, _path: (rewrite(child),))
            case (
                SemanticList(key=key)
                | SemanticChoices(key=key)
                | SemanticNavigation(key=key)
                | SemanticTable(key=key)
                | SemanticGrid(key=key)
                | SemanticRoster(key=key)
                | SemanticMedia(key=key)
                | SemanticFormTrigger(key=key)
                | SemanticToggle(key=key)
                | SemanticDownload(key=key)
            ):
                return replace(node, key=f"{prefix}.{key}")
            case SemanticPaged(key=key):
                keyed = replace(node, key=f"{prefix}.{key}")
                return map_layout_children(keyed, "$", lambda child, _path: (rewrite(child),))
            case _:
                return map_layout_children(node, "$", lambda child, _path: (rewrite(child),))

    # `rewrite` is written over the erased union: it matches on primitive shapes, none of which
    # carry a target, and returns the same nodes rekeyed. That costs the local walker's
    # precision, not the caller's, so the target the caller passed in is what comes back.
    return cast(list[LayoutNode[RenderTargetT, ContextT]], [rewrite(node) for node in nodes])
