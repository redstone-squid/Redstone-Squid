"""Structural traversal for the authored layout tree."""

from collections.abc import Callable, Sequence
from dataclasses import fields, is_dataclass, replace

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.primitives.nodes import Break, Budget, Card, Extension, Panel, Variants
from squid_layouts.semantic import (
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

type LayoutTransform = Callable[[LayoutNode, str], Sequence[LayoutNode]]

_CHILD_FIELD_NAMES = frozenset({"children", "node", "primary", "alternates", "fallback", "variants"})


def map_layout_children(node: LayoutNode, path: str, transform: LayoutTransform) -> LayoutNode:
    """Rebuild ``node`` after transforming each of its direct layout children.

    Sequence positions accept a transform that splices zero or more nodes. Singular positions
    require exactly one result. ``path`` is diagnostic only and is extended consistently for
    every structural shape.
    """

    def many(children: Sequence[LayoutNode], parent_path: str) -> tuple[LayoutNode, ...]:
        transformed: list[LayoutNode] = []
        for index, child in enumerate(children):
            transformed.extend(transform(child, f"{parent_path}.{index}"))
        return tuple(transformed)

    def one(child: LayoutNode, child_path: str) -> LayoutNode:
        transformed = tuple(transform(child, child_path))
        if len(transformed) != 1:
            message = f"{child_path}: this structural position requires exactly one node"
            raise LayoutInvariantError(message)
        return transformed[0]

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
            return replace(node, children=many(children, path))  # pyrefly: ignore[bad-argument-type]
        case Items(items=items):
            return replace(
                node,
                items=tuple(
                    replace(item, children=many(item.children, f"{path}.item.{index}"))
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
            return replace(node, node=one(child, f"{path}.node"))
        case FallbackContent(primary=primary, alternates=alternates):
            return replace(
                node,
                primary=one(primary, f"{path}.primary"),
                alternates=tuple(
                    one(alternate, f"{path}.alternate.{index}") for index, alternate in enumerate(alternates)
                ),
            )
        case Extension(fallback=fallback):
            return replace(
                node,
                fallback=one(fallback, f"{path}.fallback"),  # pyrefly: ignore[bad-argument-type]
            )
        case Variants(variants=variants):
            return replace(
                node,
                variants=tuple(
                    replace(
                        variant,
                        nodes=many(  # pyrefly: ignore[bad-argument-type]
                            variant.nodes,
                            f"{path}.variant.{index}",
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
