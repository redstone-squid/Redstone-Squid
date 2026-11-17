"""Dependency-leaf static vocabulary for render targets and adapter families."""

from typing import Generic, TypeVar


class RenderTarget:
    """Marker for portable values renderable by every semantic target."""


class DiscordTarget(RenderTarget):
    """Marker for values renderable to any Discord component mode."""


class ComponentsV2Target(DiscordTarget):
    """Marker for Discord Components V2 renderables."""


class ClassicTarget(DiscordTarget):
    """Marker for classic Discord message renderables."""


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
