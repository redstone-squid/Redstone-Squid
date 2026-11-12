"""Structural traversal for the authored layout tree."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import cast

from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    Card,
    Code,
    ControlGroup,
    Extension,
    Footer,
    Heading,
    Lines,
    Panel,
    Row,
    SelectMenu,
    Text,
    Variants,
)
from squid_ui.primitives.nodes import Section as PrimitiveSection
from squid_ui.semantic import (
    ActionControl as SemanticActionControl,
)
from squid_ui.semantic import (
    ActionControls as SemanticActionControls,
)
from squid_ui.semantic import (
    AnyLayoutNode,
    Article,
    Aside,
    BestEffort,
    Block,
    Budgeted,
    Cluster,
    Details,
    FallbackContent,
    Group,
    Items,
    KeepWithNext,
    LayoutNode,
    OptionalContent,
    Paged,
    Section,
    Spilled,
    Stack,
    Themed,
    Truncated,
    Unbreakable,
)
from squid_ui.semantic import (
    Choices as SemanticChoices,
)
from squid_ui.semantic import ControlGroup as SemanticControlGroup
from squid_ui.semantic import (
    Details as SemanticDetails,
)
from squid_ui.semantic import Download as SemanticDownload
from squid_ui.semantic import (
    FormTrigger as SemanticFormTrigger,
)
from squid_ui.semantic import Grid as SemanticGrid
from squid_ui.semantic import (
    Items as SemanticItems,
)
from squid_ui.semantic import (
    Link as SemanticLink,
)
from squid_ui.semantic import (
    List as SemanticList,
)
from squid_ui.semantic import (
    Media as SemanticMedia,
)
from squid_ui.semantic import (
    Navigation as SemanticNavigation,
)
from squid_ui.semantic import Paged as SemanticPaged
from squid_ui.semantic import Roster as SemanticRoster
from squid_ui.semantic import RoutedActionControl as SemanticRoutedActionControl
from squid_ui.semantic import (
    Table as SemanticTable,
)
from squid_ui.semantic import Toggle as SemanticToggle
from squid_ui.target_types import RenderTarget

type LayoutTransform[RenderTargetT: RenderTarget, ContextT = object] = Callable[
    [LayoutNode[RenderTargetT, ContextT], str], Sequence[LayoutNode[RenderTargetT, ContextT]]
]
type _LayoutRoute = tuple[_FieldStep | _IndexStep | _SequenceStep, ...]
type _RoutedLayoutTransform[RenderTargetT: RenderTarget, ContextT = object] = Callable[
    [LayoutNode[RenderTargetT, ContextT], str, _LayoutRoute], Sequence[LayoutNode[RenderTargetT, ContextT]]
]

_CHILD_FIELD_NAMES = frozenset({"children", "node", "primary", "alternates", "fallback", "variants"})


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


def map_layout_children[RenderTargetT: RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT], path: str, transform: LayoutTransform[RenderTargetT, ContextT]
) -> LayoutNode[RenderTargetT, ContextT]:
    """Rebuild ``node`` after transforming each of its direct layout children.

    Sequence positions accept a transform that splices zero or more nodes. Singular positions
    require exactly one result. ``path`` is diagnostic only and is extended consistently for
    every structural shape.
    """

    return _map_layout_children_routed(node, path, (), lambda child, child_path, _route: transform(child, child_path))


def _map_layout_children_routed[RenderTargetT: RenderTarget, ContextT = object](
    node: LayoutNode[RenderTargetT, ContextT],
    path: str,
    route: _LayoutRoute,
    transform: _RoutedLayoutTransform[RenderTargetT, ContextT],
) -> LayoutNode[RenderTargetT, ContextT]:
    """Rebuild a node while identifying where each transformed child lands."""

    def many(
        children: Sequence[LayoutNode[RenderTargetT, ContextT]], parent_path: str, field: str
    ) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
        transformed: list[LayoutNode[RenderTargetT, ContextT]] = []
        for index, child in enumerate(children):
            transformed.extend(
                transform(child, f"{parent_path}.{index}", (*route, _SequenceStep(field, len(transformed))))
            )
        return tuple(transformed)

    def one(
        child: LayoutNode[RenderTargetT, ContextT], child_path: str, child_route: _LayoutRoute
    ) -> LayoutNode[RenderTargetT, ContextT]:
        transformed = tuple(transform(child, child_path, child_route))
        if len(transformed) != 1:
            message = f"{child_path}: this structural position requires exactly one node"
            raise LayoutInvariantError(message)
        return transformed[0]

    # Authored children use the open LayoutNode type, but primitive containers declare the
    # closed Node union for planner matching. The suppressions preserve the caller's generic
    # child type while rebuilding those same runtime containers.
    match node:
        case (
            Group(children=children)
            | Stack(children=children)
            | Cluster(children=children)
            | Themed(children=children)
            | Block(children=children)
            | Section(children=children)
            | Article(children=children)
            | Aside(children=children)
            | Details(children=children)
            | Panel(children=children)
            | Budget(children=children)
            | Break(children=children)
            | Card(children=children)
        ):
            return replace(node, children=many(children, path, "children"))  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
        case Items(items=items):
            return replace(
                node,
                items=tuple(
                    replace(
                        item,
                        children=_map_many_at(
                            item.children,
                            f"{path}.item.{index}",
                            (*route, _IndexStep("items", index)),
                            "children",
                            transform,
                        ),
                    )
                    for index, item in enumerate(items)
                ),
            )
        case (
            Truncated(node=child)
            | Spilled(node=child)
            | OptionalContent(node=child)
            | BestEffort(node=child)
            | Budgeted(node=child)
            | Unbreakable(node=child)
            | KeepWithNext(node=child)
            | Paged(node=child)
        ):
            return replace(node, node=one(child, f"{path}.node", (*route, _FieldStep("node"))))
        case FallbackContent(primary=primary, alternates=alternates):
            return replace(
                node,
                primary=one(primary, f"{path}.primary", (*route, _FieldStep("primary"))),
                alternates=tuple(
                    one(
                        alternate,
                        f"{path}.alternate.{index}",
                        (*route, _IndexStep("alternates", index)),
                    )
                    for index, alternate in enumerate(alternates)
                ),
            )
        case Extension(fallback=fallback):
            return replace(
                node,
                fallback=one(  # pyrefly: ignore[bad-argument-type]
                    fallback,  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
                    f"{path}.fallback",
                    (*route, _FieldStep("fallback")),
                ),
            )
        case Variants(variants=variants):
            return replace(
                node,
                variants=tuple(
                    replace(
                        variant,
                        nodes=_map_many_at(  # pyrefly: ignore[bad-argument-type]
                            variant.nodes,  # pyright: ignore[reportArgumentType]
                            f"{path}.variant.{index}",
                            (*route, _IndexStep("variants", index)),
                            "nodes",
                            transform,  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
                        ),
                    )
                    for index, variant in enumerate(variants)
                ),
            )
        case _:
            structural_fields = (
                _CHILD_FIELD_NAMES.intersection(field.name for field in fields(node))
                if is_dataclass(node) and not isinstance(node, type)
                else frozenset()
            )
            if structural_fields:
                names = ", ".join(sorted(structural_fields))
                message = f"{path}: {type(node).__name__} has unregistered layout fields: {names}"
                raise LayoutInvariantError(message)
            return node


def _map_many_at[RenderTargetT: RenderTarget, ContextT = object](
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


def _namespace[RenderTargetT: RenderTarget, ContextT = object](
    nodes: list[LayoutNode[RenderTargetT, ContextT]], prefix: str
) -> list[LayoutNode[RenderTargetT, ContextT]]:
    """Rewrite an embedded subtree's control keys under ``prefix``.

    Explicit control keys are scoped under the embed path, so inserting a sibling cannot
    change the identity of any existing action.
    """

    def key_for(node: Button | SelectMenu) -> str:
        return f"{prefix}.{node.key}"

    def rewrite_text[T: Text | Heading | Footer | Code | Lines](node: T) -> T:
        overflow = node.overflow
        if isinstance(overflow, Paginate) and overflow.key is not None:
            return replace(node, overflow=replace(overflow, key=f"{prefix}.{overflow.key}"))
        return node

    def rewrite_item[T](item: T) -> T:
        return replace(item, key=key_for(item)) if isinstance(item, Button) else item  # pyrefly: ignore

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
        match node:
            case SemanticActionControls(items=items, key=key):
                return replace(
                    node,
                    key=f"{prefix}.{key}",
                    items=tuple(rewrite_semantic_action(item) for item in items),
                )
            case SemanticDetails(key=key) | SemanticItems(key=key):
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
            case Text() | Heading() | Footer() | Code() | Lines():
                return rewrite_text(node)
            case Row(items=items):
                return Row(items=tuple(rewrite_item(item) for item in items))
            case ControlGroup(items=items):
                return ControlGroup(items=tuple(rewrite_item(item) for item in items))
            case PrimitiveSection(texts=texts, accessory=accessory):
                return PrimitiveSection(
                    texts=tuple(rewrite_text(text) for text in texts), accessory=rewrite_item(accessory)
                )
            case SelectMenu():
                return replace(node, key=key_for(node))
            case _:
                return map_layout_children(node, "$", lambda child, _path: (rewrite(child),))

    # `rewrite` is written over the erased union: it matches on primitive shapes, none of which
    # carry a target, and returns the same nodes rekeyed. That costs the local walker's
    # precision, not the caller's, so the target the caller passed in is what comes back.
    return cast(list[LayoutNode[RenderTargetT, ContextT]], [rewrite(node) for node in nodes])
