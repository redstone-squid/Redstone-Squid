"""Find the inside of a framed tunnel in a sparse block map.

Picture looking down a Z hallway at one XY slice. Each # below is a wall.
The dots mark the interior we want to measure. Air outside the frame can
reach the edge of the slice. The dots can reach only one another.

    #####
    #..##
    #...#
    #####

Flooding the slice from its outside edge leaves exactly those five dots.
Stack the results for successive Z coordinates to recover the 3D interior.
The walls can have an irregular outline or enclose a pillar. A missing side
lets the flood into that slice, so an incomplete capture can yield no hallway.
"""

from collections import defaultdict, deque
from collections.abc import Iterator, Mapping, Set
from dataclasses import dataclass
from itertools import product
from typing import Literal

from squid.core.schematics.domain.values import Vector3

type World = Mapping[Vector3, str]
type Point = tuple[int, int]
type Seal = Literal["sealed", "passage_remains", "unknown"]


@dataclass(frozen=True, slots=True)
class PassageAxis:
    """Coordinate axes as seen when looking through the door.

    For travel along Z, a face coordinate (across, up) is just (x, y) and
    depth is z. For travel along X, across is z instead. A vertical shaft
    uses its X/Z footprint. The coordinates are absolute along each axis.
    """

    across: int
    up: int
    travel: int

    def face(self, position: Vector3) -> Point:
        return position[self.across], position[self.up]

    def position(self, face: Point, depth: int) -> Vector3:
        xyz = [0, 0, 0]
        xyz[self.across], xyz[self.up] = face
        xyz[self.travel] = depth
        return xyz[0], xyz[1], xyz[2]


AXES = (PassageAxis(2, 1, 0), PassageAxis(0, 2, 1), PassageAxis(0, 1, 2))


def neighbours(pos: Vector3) -> Iterator[Vector3]:
    x, y, z = pos
    for delta in (-1, 1):
        yield x + delta, y, z
        yield x, y + delta, z
        yield x, y, z + delta


@dataclass(frozen=True, slots=True)
class Hallway:
    """One connected interior in source coordinates, with a mouth at each axial extreme.

    Each transverse slice contributes its own interior. A narrower middle slice,
    an internal pillar or an asymmetric outline need no special shape definition.
    The extent ends where the capture stops providing an enclosing perimeter.
    """

    axis: int
    interior: frozenset[Vector3]
    entrance: frozenset[Vector3]
    exit: frozenset[Vector3]

    def connects(self, blocked: Set[Vector3]) -> bool:
        """Test face-connected voxel passage between the two mouths.

        Only traverse the observed interior. Free space beside a freestanding
        frame would otherwise let the search walk around a closed door.
        """
        visited = set(self.entrance - blocked)
        queue = deque(visited)
        while queue:
            cell = queue.popleft()
            if cell in self.exit:
                return True
            for other in neighbours(cell):
                if other in self.interior and other not in visited and other not in blocked:
                    visited.add(other)
                    queue.append(other)
        return False

    def seal(self, closed_blocks: World, full_blocks: Set[str]) -> Seal:
        """Can a face-connected path of empty cells still cross the closed hallway?

        Try two extremes for a slab or any other partially occupied cell. First
        allow passage through all partial blocks: a failed crossing proves that
        full cubes alone seal this interior. Then treat every occupied cell as
        blocked: a successful crossing proves that a path made of air remains.
        If those tests disagree, exact sub-block geometry would be needed.
        """
        full_cubes = {cell for cell, state in closed_blocks.items() if state in full_blocks}
        if not self.connects(full_cubes):
            return "sealed"
        if self.connects(closed_blocks.keys()):
            return "passage_remains"
        return "unknown"


def enclosed_slice(walls: set[Point]) -> set[Point]:
    """Remove the outside air of a transverse slice.

    Padding puts the starting point outside even when the frame touches the
    capture boundary. Anything still unvisited is separated from the outside
    by wall cells. Flooding from a moved block instead would mistake the size
    of a partial plug for the size of the opening it sits in.
    """
    if not walls:
        return set()
    xs, ys = zip(*walls, strict=True)
    left, right = min(xs) - 1, max(xs) + 1
    bottom, top = min(ys) - 1, max(ys) + 1
    unvisited = set(product(range(left, right + 1), range(bottom, top + 1))) - walls
    start = (left, bottom)
    unvisited.remove(start)
    queue = [start]
    while queue:
        x, y = queue.pop()
        for other in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if other in unvisited:
                unvisited.remove(other)
                queue.append(other)
    return unvisited


def _changed_interiors(interior: set[Vector3], changes: set[Vector3]) -> Iterator[set[Vector3]]:
    """Group touching interior cells, starting only in tunnels affected by the input.

    A moved cell gives us a place to start. Its connected component includes
    unchanged air beside it, which is how a partial panel keeps its true
    hallway width. Consume visited cells so multiple moved blocks in the same
    opening give one result.
    """
    for seed in sorted(interior & changes):
        if seed not in interior:
            continue
        interior.remove(seed)
        component = {seed}
        pending = [seed]
        while pending:
            for other in neighbours(pending.pop()):
                if other in interior:
                    interior.remove(other)
                    component.add(other)
                    pending.append(other)
        yield component


def hallways(world: World, full_blocks: Set[str], axis: PassageAxis, changes: set[Vector3]) -> Iterator[Hallway]:
    """Yield framed interiors with a straight approach from both ends.

    `world` is the proposed open snapshot. Only the full block states in
    `full_blocks` can establish walls. Each slice is measured independently,
    so its shape can vary with depth. Finally inspect the mouths: an enclosed
    machinery cavity capped at either end cannot serve as a straight tunnel.
    """
    walls_by_depth: defaultdict[int, set[Point]] = defaultdict(set)
    # Along each line of sight, the first and last occupied cells are enough
    # to tell whether a builder has a clear approach to either mouth.
    occupied_span: dict[Point, tuple[int, int]] = {}
    for position, state in world.items():
        face = axis.face(position)
        depth = position[axis.travel]
        first, last = occupied_span.get(face, (depth, depth))
        occupied_span[face] = min(first, depth), max(last, depth)
        if state in full_blocks:
            walls_by_depth[depth].add(face)

    interior: set[Vector3] = set()
    for depth, walls in walls_by_depth.items():
        interior.update(axis.position(face, depth) for face in enclosed_slice(walls))

    for tunnel in _changed_interiors(interior, changes):
        front = min(cell[axis.travel] for cell in tunnel)
        back = max(cell[axis.travel] for cell in tunnel)
        entrance: set[Vector3] = set()
        exit_cells: set[Vector3] = set()
        for cell in tunnel:
            face = axis.face(cell)
            span = occupied_span.get(face)
            if cell[axis.travel] == front and (span is None or span[0] >= front):
                entrance.add(cell)
            if cell[axis.travel] == back and (span is None or span[1] <= back):
                exit_cells.add(cell)

        # Looking down an empty column must reveal a continuous strip of
        # measured interior. A bent tunnel can have two clear approaches
        # without having any such line of sight between them.
        entrance_face = {axis.face(cell) for cell in entrance}
        exit_face = {axis.face(cell) for cell in exit_cells}
        for face in entrance_face & exit_face:
            if face in occupied_span:
                continue
            if all(axis.position(face, depth) in tunnel for depth in range(front, back + 1)):
                yield Hallway(axis.travel, frozenset(tunnel), frozenset(entrance), frozenset(exit_cells))
                break
