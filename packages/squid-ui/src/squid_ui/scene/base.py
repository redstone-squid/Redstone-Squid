"""Open scene body and display-edge contracts."""

from typing import ClassVar, cast


class SceneNode:
    """A resolved display value with edges and route metadata declared by its target."""

    __slots__ = ()
    DISPLAY_FIELDS: ClassVar[tuple[str, ...]] = ()
    ROUTE_FIELDS: ClassVar[tuple[str, ...]] = ()

    def route_ids(self) -> tuple[str, ...]:
        """Return the durable route IDs carried directly by this scene value."""
        return tuple(cast(str, getattr(self, name)) for name in self.ROUTE_FIELDS)


class Body(SceneNode):
    """An open, target-owned scene body with a registered wire kind."""

    __slots__ = ()
    KIND: ClassVar[str]
