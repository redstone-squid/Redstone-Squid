"""Open scene body and display-edge contracts."""

from typing import ClassVar


class SceneNode:
    __slots__ = ()
    DISPLAY_FIELDS: ClassVar[tuple[str, ...]] = ()


class Body(SceneNode):
    __slots__ = ()
    KIND: ClassVar[str]
