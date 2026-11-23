"""Recover axis-aligned passage interiors from the open world's transverse slices."""

from collections import defaultdict, deque
from collections.abc import Iterator, Mapping, Set
from dataclasses import dataclass
from itertools import product

from squid.core.schematics.domain.models import Vector3

type World = Mapping[Vector3, str]
type Point = tuple[int, int]

# (width, height, travel) axes. For a vertical passage report its X/Z footprint.
AXES = ((2, 1, 0), (0, 2, 1), (0, 1, 2))


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
    inside = set(product(range(left, right + 1), range(bottom, top + 1))) - walls
    start = (left, bottom)
    inside.remove(start)
    queue = [start]
    while queue:
        x, y = queue.pop()
        for other in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if other in inside:
                inside.remove(other)
                queue.append(other)
    return inside


def hallways(world: World, full_blocks: Set[str], axis: int, changes: set[Vector3]) -> Iterator[Hallway]:
    """Find straight passages that contain at least one observed change.

    First reconstruct the open interior independently of the closing motion.
    Only complete block cubes supply positive evidence for a perimeter. Next
    grow each changed seed through adjacent enclosed slices, visiting a passage
    once even when hundreds of its cells changed. Finally check its approaches
    against the rest of the schematic to exclude enclosed machinery pockets.
    """
    u, v, _ = AXES[axis]
    slices: defaultdict[int, set[Point]] = defaultdict(set)
    columns: defaultdict[Point, list[int]] = defaultdict(list)
    for p, state in world.items():
        uv = p[u], p[v]
        columns[uv].append(p[axis])
        if state in full_blocks:
            slices[p[axis]].add(uv)

    interior: set[Vector3] = set()
    for depth, walls in slices.items():
        for x, y in enclosed_slice(walls):
            p = [0, 0, 0]
            p[u], p[v], p[axis] = x, y, depth
            interior.add((p[0], p[1], p[2]))

    for seed in sorted(changes):
        if seed not in interior:
            continue
        interior.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            for other in neighbours(queue.pop()):
                if other in interior:
                    interior.remove(other)
                    component.add(other)
                    queue.append(other)
        low = min(p[axis] for p in component)
        high = max(p[axis] for p in component)
        entrance = frozenset(p for p in component if p[axis] == low and all(d >= low for d in columns[p[u], p[v]]))
        exit_cells = frozenset(p for p in component if p[axis] == high and all(d <= high for d in columns[p[u], p[v]]))
        # A straight passage must have a line through both approaches. A bent
        # pocket can otherwise join two unrelated openings in adjacent slices.
        rays = {(p[u], p[v]) for p in entrance} & {(p[u], p[v]) for p in exit_cells}
        if not any(not columns[ray] for ray in rays):
            continue
        yield Hallway(axis, frozenset(component), entrance, exit_cells)
