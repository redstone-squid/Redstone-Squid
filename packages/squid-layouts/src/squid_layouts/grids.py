"""Immutable cell declarations shared by the semantic vocabulary and exact grids.

Public, but deliberately not part of the root namespace: `sl.grid` is the factory that builds
a grid, and a module of that name bound on the package would shadow it. Downstream pattern
and transport packages import `squid_layouts.grids` directly.
"""

from dataclasses import dataclass

from squid_layouts.palette import Tone
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class GridCell:
    """One stable selectable position in a grid."""

    key: str
    label: TextLike
    available: bool = True
    tone: Tone = Tone.NEUTRAL

    def __post_init__(self) -> None:
        if not self.key:
            message = "grid cell key must not be empty"
            raise ValueError(message)


def validate_grid(cells: tuple[GridCell, ...], columns: int) -> None:
    """Validate shape and identity shared by both grid authoring surfaces."""
    if not cells:
        message = "grid needs at least one cell"
        raise ValueError(message)
    if columns < 1:
        message = "grid columns must be positive"
        raise ValueError(message)
    if len({cell.key for cell in cells}) != len(cells):
        message = "grid cell keys must be unique"
        raise ValueError(message)


__all__ = ["GridCell"]
