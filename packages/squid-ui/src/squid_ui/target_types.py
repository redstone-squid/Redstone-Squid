"""Dependency-leaf static vocabulary for portable render targets."""

from typing import Generic, TypeVar


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
