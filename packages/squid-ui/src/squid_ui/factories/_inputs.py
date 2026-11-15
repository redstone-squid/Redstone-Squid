"""Validate shorthand children and collection entries for semantic factories."""

from collections.abc import Iterable, Iterator, Mapping
from string.templatelib import Template
from types import UnionType
from typing import Any, Literal, NoReturn, TypeAliasType, TypeIs, cast, get_args, get_origin

from squid_i18n import Text
from squid_ui.authoring.semantic import (
    BuiltinLayoutNode,
    LayoutNode,
    Paragraph,
    PortableNode,
)
from squid_ui.content.text import TextLike
from squid_ui.target_types import Renderable, RenderTarget

type TextValue = TextLike
"""Author text: literal strings and t-strings, or explicitly formatted text."""

type Conditional[ItemT] = ItemT | Literal[False] | None
"""One value, or an omitted one.

``None`` and ``False`` are skipped so ``cond and node`` composes directly. In a container's
child position an empty string is skipped too, because ``text and sl.paragraph(text)``
evaluates to ``""`` when the text is empty. ``True`` is deliberately *not* accepted:
``x and y`` evaluates to ``y`` or to the falsy ``x``, so a bare ``True`` can only come from a
mistake. The same narrowness makes ``count and node`` — which would render a literal ``0`` in
looser designs — a type error rather than a surprise.
"""

type ChildLike[RenderTargetT = RenderTarget, ContextT = object] = Conditional[
    LayoutNode[RenderTargetT, ContextT] | TextLike | Template
]
"""Anything acceptable in a container's child position; text is promoted to `Paragraph`.

`RenderTargetT` is what makes a nested dialect mistake a type error. A container factory solves it
from its children, so a `Panel` two levels down inside an otherwise portable document makes
the whole document `ComponentsV2Target`, and handing that to a classic target does not
type-check. See `docs/plans/squid-ui-redesign/spikes/73/` for the measurement that chose
plain inference over an overload ladder, and for the one case it does not catch.
"""


def _node_types(annotation: object) -> Iterator[type]:
    """Flatten aliases and unions into concrete classes for runtime membership tests.

    Parameterized containers have a runtime origin rather than being classes themselves;
    follow that origin so generic layout nodes remain in the accepted vocabulary.
    """
    if isinstance(annotation, TypeAliasType):
        yield from _node_types(annotation.__value__)
    elif isinstance(annotation, UnionType):
        for member in get_args(annotation):
            yield from _node_types(member)
    elif isinstance(annotation, type):
        yield annotation
    elif isinstance(origin := get_origin(annotation), type):
        yield origin
    elif isinstance(origin, TypeAliasType):
        # A subscripted alias like `SemanticNode[RenderTargetT]`: its origin is the alias
        # itself, so recurse into it the same way an unsubscripted alias is flattened.
        yield from _node_types(origin)


# Derived from the union rather than hand-listed, so a new node type is accepted the moment
# it joins `BuiltinLayoutNode`.
_BUILTIN_TYPES: tuple[type, ...] = tuple(_node_types(BuiltinLayoutNode))
_PORTABLE_TYPES: tuple[type, ...] = tuple(_node_types(PortableNode))


def is_layout_node(value: object) -> TypeIs[Renderable[Any, Any]]:
    """Whether value inherits Renderable, including application-defined layout nodes.

    Use is_builtin_layout_node when a traversal requires the framework's closed union.
    """
    return isinstance(value, Renderable)


def is_builtin_layout_node(value: object) -> TypeIs[BuiltinLayoutNode]:
    """Whether value belongs to the framework-defined layout vocabulary."""
    return isinstance(value, _BUILTIN_TYPES)


def is_portable_node(value: object) -> TypeIs[PortableNode[Any]]:
    """True when `value` belongs to the closed portable vocabulary every planner answers for.

    Distinguishes the portable union from the `Renderable` escape hatch: a Discord primitive
    is a layout node but not a portable one.
    """
    return isinstance(value, _PORTABLE_TYPES)


def _reject(value: object, origin: str, index: int) -> NoReturn:
    if value is True:
        detail = (
            "True is not content; `cond and node` evaluates to the node or to the condition, "
            "so only None and False can stand for an omitted child"
        )
    elif isinstance(value, str | Text | Template):
        # Only collection factories reject text; container children promote it to a Paragraph.
        detail = "text is not an entry here; build one with the matching factory"
    elif isinstance(value, Mapping):
        detail = f"a mapping is not content; unpack what you meant, e.g. {origin[:-1]}*mapping.values())"
    elif isinstance(value, Iterable):
        detail = f"a {type(value).__name__} is not content; unpack it, e.g. {origin[:-1]}*entries)"
    else:
        detail = f"{type(value).__name__} is not content"
    message = f"{origin} argument {index}: {detail}"
    raise TypeError(message)


def _children[RenderTargetT = RenderTarget, ContextT = object](
    values: tuple[ChildLike[RenderTargetT, ContextT], ...], origin: str
) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
    collected: list[LayoutNode[RenderTargetT, ContextT]] = []
    for index, value in enumerate(values):
        if value is None or value is False or value == "":
            continue
        if isinstance(value, str | Text | Template):
            # A paragraph renders on every target, but its own annotation names the base
            # `RenderTarget`, so it cannot be spelled as satisfying an arbitrary one.
            collected.append(cast(LayoutNode[RenderTargetT, ContextT], Paragraph(Text(value))))
        elif is_layout_node(value):
            collected.append(value)
        else:
            _reject(value, origin, index)
    return tuple(collected)


def _collect[ItemT](values: Iterable[Conditional[ItemT]], kinds: tuple[type, ...], origin: str) -> tuple[ItemT, ...]:
    """Drop omitted entries and reject anything that is not one of `kinds`.

    `kinds` is the erased runtime table, not `type[ItemT]`: a generic entry such as `Item`
    has no runtime class per render target, so tying the two would solve `ItemT` to the erased class
    and lose the caller's.
    """
    collected: list[ItemT] = []
    for index, value in enumerate(values):
        if value is None or value is False:
            continue
        if isinstance(value, kinds):
            collected.append(value)
        else:
            _reject(value, origin, index)
    return tuple(collected)
