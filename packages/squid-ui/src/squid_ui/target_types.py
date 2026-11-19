"""Dependency-leaf static vocabulary for portable render targets."""

from dataclasses import dataclass, fields, is_dataclass
from typing import Generic, TypeVar

from squid_ui.errors import LayoutInvariantError


class RenderTarget:
    """Marker for portable values renderable by every semantic target."""


# Explicit variance also preserves these constraints when Component inherits Renderable.
RenderTargetT = TypeVar("RenderTargetT", contravariant=True, default=RenderTarget)
ContextT = TypeVar("ContextT", contravariant=True, default=object)


class Renderable(Generic[RenderTargetT, ContextT]):
    """A value whose accepted protocol target is tracked by the type checker."""

    __slots__ = ()
    """Empty, so a `slots=True` node subclass really is slotted.

    A base without `__slots__` grants every subclass a `__dict__`, which silently defeated
    the `@dataclass(frozen=True, slots=True)` on every node in the package.
    """

    def _accepts_context(self, context: ContextT, /) -> None:
        del context

    def _accepts_target(self, target: RenderTargetT, /) -> None:
        del target


# Field names that suggest a class holds child nodes it never declared.
_CHILD_FIELD_NAMES = frozenset({"children", "node", "nodes", "primary", "alternates", "fallback", "variants"})


@dataclass(frozen=True, slots=True)
class Structure:
    """The fields of a UI class that hold other UI values.

    A class declares this as ``STRUCTURE`` so layout rewriting, planning, and selectors walk
    the same edges. Subclasses inherit it. A class without one is a leaf. Layout rewriting
    rebuilds nodes with ``dataclasses.replace``, so a class with layout fields must be a dataclass.

    Attributes:
        layout: Fields holding one layout node, a tuple of them, or None, in display order.
        nested: ``(field, child)`` pairs for a tuple of records whose ``child`` field holds
            layout nodes, such as each item's children in ``Items.items``.
        query: Selector edges in display order, when they differ from the layout and nested
            fields. Selectors also visit records and fields that layout rewriting leaves
            alone, such as a section's heading.
    """

    layout: tuple[str, ...] = ()
    nested: tuple[tuple[str, str], ...] = ()
    query: tuple[str, ...] | None = None

    @property
    def query_fields(self) -> tuple[str, ...]:
        return self.query if self.query is not None else (*self.layout, *(field for field, _ in self.nested))

    @classmethod
    def of(cls, value: object) -> Structure:
        """Return the structure declared by ``value``'s class, or an empty one for a leaf.

        Raises:
            LayoutInvariantError: A dataclass `Renderable` without a declaration has a field
                named like a child slot, so treating it as a leaf would hide its children.
        """
        structure = getattr(type(value), "STRUCTURE", None)
        if structure is not None:
            return structure
        if isinstance(value, Renderable) and is_dataclass(value):
            undeclared = _CHILD_FIELD_NAMES.intersection(field.name for field in fields(value))
            if undeclared:
                names = ", ".join(sorted(undeclared))
                message = f"{type(value).__name__} has unregistered layout fields: {names}; declare STRUCTURE"
                raise LayoutInvariantError(message)
        return _LEAF


_LEAF = Structure()
