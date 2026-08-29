"""Structural traversal for the authored layout tree."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace

from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.nodes import Break, Budget, Card, Extension, Panel, Variants
from squid_ui.semantic import (
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
from squid_ui.target_types import RenderTarget

type LayoutTransform[RenderTargetT: RenderTarget] = Callable[
    [LayoutNode[RenderTargetT], str], Sequence[LayoutNode[RenderTargetT]]
]
type _LayoutRoute = tuple[_FieldStep | _IndexStep | _SequenceStep, ...]
type _RoutedLayoutTransform[RenderTargetT: RenderTarget] = Callable[
    [LayoutNode[RenderTargetT], str, _LayoutRoute], Sequence[LayoutNode[RenderTargetT]]
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


def map_layout_children[RenderTargetT: RenderTarget](
    node: LayoutNode[RenderTargetT], path: str, transform: LayoutTransform[RenderTargetT]
) -> LayoutNode[RenderTargetT]:
    """Rebuild ``node`` after transforming each of its direct layout children.

    Sequence positions accept a transform that splices zero or more nodes. Singular positions
    require exactly one result. ``path`` is diagnostic only and is extended consistently for
    every structural shape.
    """

    return _map_layout_children_routed(node, path, (), lambda child, child_path, _route: transform(child, child_path))


def _map_layout_children_routed[RenderTargetT: RenderTarget](
    node: LayoutNode[RenderTargetT],
    path: str,
    route: _LayoutRoute,
    transform: _RoutedLayoutTransform[RenderTargetT],
) -> LayoutNode[RenderTargetT]:
    """Rebuild a node while identifying where each transformed child lands."""

    def many(
        children: Sequence[LayoutNode[RenderTargetT]], parent_path: str, field: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        transformed: list[LayoutNode[RenderTargetT]] = []
        for index, child in enumerate(children):
            transformed.extend(
                transform(child, f"{parent_path}.{index}", (*route, _SequenceStep(field, len(transformed))))
            )
        return tuple(transformed)

    def one(child: LayoutNode[RenderTargetT], child_path: str, child_route: _LayoutRoute) -> LayoutNode[RenderTargetT]:
        transformed = tuple(transform(child, child_path, child_route))
        if len(transformed) != 1:
            message = f"{child_path}: this structural position requires exactly one node"
            raise LayoutInvariantError(message)
        return transformed[0]

    # The suppressions below all name one mismatch, not four. This walker rewrites the
    # *authored* tree, so it speaks `LayoutNode[RenderTargetT]` -- open, and parameterized by
    # the caller's dialect. But the primitive containers it has to rebuild (`Break`, `Budget`,
    # `Extension.fallback`, `Variant.nodes`) declare their child fields as `Node`: the closed,
    # already-lowered union, which is what lets the planner match over them exhaustively. The
    # two are the same objects at runtime and neither declaration is wrong for its own job.
    # Closing the gap means making those containers generic in their children's target, which
    # is a change to the primitive vocabulary rather than to this traversal.
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
            return replace(node, children=many(children, path, "children"))  # pyrefly: ignore[bad-argument-type]
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
                    fallback,  # pyrefly: ignore[bad-argument-type]
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
                            variant.nodes,
                            f"{path}.variant.{index}",
                            (*route, _IndexStep("variants", index)),
                            "nodes",
                            transform,  # pyrefly: ignore[bad-argument-type]
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


def _map_many_at[RenderTargetT: RenderTarget](
    children: Sequence[LayoutNode[RenderTargetT]],
    path: str,
    route: _LayoutRoute,
    field: str,
    transform: _RoutedLayoutTransform[RenderTargetT],
) -> tuple[LayoutNode[RenderTargetT], ...]:
    transformed: list[LayoutNode[RenderTargetT]] = []
    for index, child in enumerate(children):
        transformed.extend(transform(child, f"{path}.{index}", (*route, _SequenceStep(field, len(transformed)))))
    return tuple(transformed)
