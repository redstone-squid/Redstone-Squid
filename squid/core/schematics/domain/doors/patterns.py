"""Fit a pattern to the occupied cells of a hallway in its proposed closed state.

Imagine two adjacent 3 by 3 sheets along Z. To try a flat
panel at z=7, check the nine required positions at z=7. The sheet at z=8
doesn't enter that check. Trying z=8 gives a second match, and trying a
two-layer plug gives a third. Each match carries its own coordinates so
callers can see exactly what differs.

Each pattern specifies which cells must become occupied and which must stay
clear when the door closes. Everything outside both sets is unconstrained. Generate these
sets once in coordinates relative to the passage, then try the observed
depths as possible placements. Material checks happen after placement.
"""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from squid.core.schematics.domain.doors.hallway import AXES, Hallway, PassageAxis, Point, World
from squid.core.schematics.domain.values import Vector3


@dataclass(frozen=True, slots=True)
class PatternMatch:
    """One matching arrangement, with its exact blocks in schematic coordinates.

    Depth locates the reference sheet along the hallway axis. Facing gives
    the direction of increasing layer offsets (+1 or -1), or zero for a
    single sheet. A funnel's reference sheet contains its outer rim.
    """

    arrangement: str
    depth: int
    facing: int
    compositions: tuple[str, ...]
    blocks: tuple[Vector3, ...]


@dataclass(frozen=True, slots=True)
class Pattern:
    """A shape that can slide along the hallway before being matched.

    Each triple holds (across, up, depth offset). Across and up come from the
    measured opening and stay fixed. For a Z hallway, (4, 2, 1) placed at
    depth 7 requires the schematic cell (4, 2, 8).
    """

    name: str
    blocks: tuple[Vector3, ...]
    clear: tuple[Vector3, ...] = ()
    facing: int = 0

    @classmethod
    def for_outline(cls, outline: set[Point]) -> Iterator[Pattern]:
        """Generate the flat, stepped and bar arrangements that fit this outline.

        The ring number of a cell is its distance to the nearest edge of a
        rectangle. In a 5 by 5 funnel the outer rim sits at depth 0, the inner
        rim at 1 and the center at 2. Negating these offsets turns it around.
        The flat arrangement works with any outline, including one-cell strips.
        """
        plane = tuple((x, y, 0) for x, y in sorted(outline))
        yield cls("flat", plane)
        xs, ys = zip(*outline, strict=True)
        left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
        width, height = right - left + 1, top - bottom + 1
        if len(outline) == width * height and min(width, height) >= 3:
            for facing in (1, -1):
                rings = tuple((x, y, facing * min(x - left, right - x, y - bottom, top - y)) for x, y, _ in plane)
                yield cls("stepped_funnel", rings, facing=facing)

        # A bar leaves the rest of its own plane clear. Blocks before or
        # behind the bar can still be present in the closed build.
        for name, coordinate, start, size in (("center_bar", 0, left, width), ("horizontal_bar", 1, bottom, height)):
            if size >= 3 and size % 2:
                middle = start + size // 2
                bars = tuple(p for p in plane if p[coordinate] == middle)
                gaps = tuple(p for p in plane if p[coordinate] != middle)
                if bars:
                    yield cls(name, bars, gaps)

    def place(self, hallway: Hallway, depth: int, opened: World, closed: World) -> PatternMatch | None:
        """Translate the template to one depth and test its requirements in both snapshots."""
        axis = AXES[hallway.axis]
        occupied = []
        for x, y, offset in self.blocks:
            cell = axis.position((x, y), depth + offset)
            if cell not in hallway.interior or cell in opened or cell not in closed:
                return None
            occupied.append(cell)
        for x, y, offset in self.clear:
            cell = axis.position((x, y), depth + offset)
            if cell not in hallway.interior or cell in closed:
                return None
        return PatternMatch(
            self.name, depth, self.facing, compositions(closed, occupied, axis), tuple(sorted(occupied))
        )


def compositions(observed: Mapping[Vector3, str], blocks: Sequence[Vector3], axis: PassageAxis) -> tuple[str, ...]:
    """Describe materials at the candidate door positions.

    In a checkerboard each square of the same parity has the same material.
    Grouping by parity works for either starting color and for translated doors.
    """
    by_parity: tuple[set[str], set[str]] = (set(), set())
    for position in blocks:
        across, up = axis.face(position)
        material = observed[position].partition("[")[0]
        by_parity[(across + up) % 2].add(material)
    even, odd = by_parity
    if even | odd == {"minecraft:glass"}:
        return ("glass",)
    if len(even) == len(odd) == 1 and even != odd:
        return ("checkerboard",)
    return ()


def matches(hallway: Hallway, observed: World, opened: World) -> Iterator[PatternMatch]:
    """Try each occupied depth as the reference sheet of a possible door.

    A tunnel can narrow along its length. Size the template to the opening
    at the proposed sheet, so a small plug can match where the tunnel narrows.
    """
    axis = AXES[hallway.axis]
    outlines: defaultdict[int, set[Point]] = defaultdict(set)
    for cell in hallway.interior:
        outlines[cell[axis.travel]].add(axis.face(cell))

    layers: dict[int, tuple[Vector3, ...]] = {}
    for depth in sorted({cell[axis.travel] for cell in observed}):
        for pattern in Pattern.for_outline(outlines[depth]):
            match = pattern.place(hallway, depth, opened, observed)
            if match is not None:
                if match.arrangement == "flat":
                    layers[depth] = match.blocks
                yield match

    # Grow one plug from each possible front sheet. Starting from every sheet
    # retains the distinction between a thick door and a shallow door with extras.
    for front, layer in layers.items():
        thick = list(layer)
        back = front + 1
        while back in layers and outlines[back] == outlines[front]:
            thick.extend(layers[back])
            yield PatternMatch("layered", front, 1, compositions(observed, thick, axis), tuple(sorted(thick)))
            back += 1
