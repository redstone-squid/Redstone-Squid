"""Portable positions and async window-source contracts."""

from dataclasses import dataclass, replace
from typing import Literal

type PositionDirection = Literal["around", "forward", "backward"]


@dataclass(frozen=True, slots=True)
class Position:
    """A source-neutral place in ordered content.

    ``around`` asks a source to keep the anchor visible, while ``forward`` and
    ``backward`` ask for the window after or before the anchor. ``offset`` is a
    zero-based location when the source can know one and otherwise remains a hint.
    """

    anchor: str | None = None
    offset: int = 0
    direction: PositionDirection = "around"


_ORIGIN = Position()


@dataclass(frozen=True, slots=True)
class PositionPolicy:
    """Resolve the shared override/anchor/staleness/stored/initial precedence ladder."""

    def resolve(
        self,
        *,
        override: Position | None = None,
        anchored: Position | None = None,
        stale: bool = False,
        stored: Position | None = None,
        initial: Position = _ORIGIN,
        reset: Position = _ORIGIN,
        upper_bound: int | None = None,
    ) -> Position:
        """Choose and clamp a position without consulting a session or source."""
        if override is not None:
            selected = override
        elif anchored is not None:
            selected = anchored
        elif stale:
            selected = reset
        elif stored is not None:
            selected = stored
        else:
            selected = initial

        offset = max(0, selected.offset)
        if upper_bound is not None:
            offset = min(offset, max(0, upper_bound))
        return replace(selected, offset=offset)


DEFAULT_POSITION_POLICY = PositionPolicy()
