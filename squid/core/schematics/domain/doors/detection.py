"""Compare repeatable endpoints of an input cycle in source coordinates."""

from collections import defaultdict
from collections.abc import Set
from dataclasses import dataclass
from math import prod
from typing import Literal

from squid.core.schematics.domain.doors.hallway import AXES, Hallway, Point, Seal, World, hallways
from squid.core.schematics.domain.doors.patterns import PatternMatch, matches
from squid.core.schematics.domain.values import Vector3

MAX_WORLD_VOLUME = 250_000
type Endpoint = Literal["initial", "actuated"]


@dataclass(frozen=True, slots=True)
class ObservedBlock:
    position: Vector3
    state: str


@dataclass(frozen=True, slots=True)
class DoorCandidate:
    """A hallway interpretation and every supported placement of a known pattern.

    Agreement is relative to the matching patterns. The closed block inventory
    also includes extras that no match selected, so a reviewer can inspect an
    arrangement outside our catalogue.
    """

    axis: int
    """Index of the travel coordinate in (x, y, z). A Z hallway has axis 2."""
    closed_endpoint: Endpoint
    seal: Seal
    interior: tuple[Vector3, ...]
    closed_blocks: tuple[ObservedBlock, ...]
    patterns: tuple[PatternMatch, ...]
    agreed_blocks: tuple[Vector3, ...]
    disputed_blocks: tuple[Vector3, ...]

    @property
    def opening_sizes(self) -> set[tuple[int, int]]:
        """Measure the opening at every plausible door placement.

        For a bar, width includes the empty lanes beside it. For a funnel,
        measure across the rim. If the pattern is unknown, consider every
        occupied slice. A taper can then leave more than one possible width.
        """
        axis = AXES[self.axis]
        depths = {match.depth for match in self.patterns}
        if not depths:
            depths = {block.position[self.axis] for block in self.closed_blocks}
        slices: defaultdict[int, list[Point]] = defaultdict(list)
        for cell in self.interior:
            depth = cell[self.axis]
            if depth in depths:
                slices[depth].append(axis.face(cell))
        sizes = set()
        for face in slices.values():
            across, up = zip(*face, strict=True)
            sizes.add((max(across) - min(across) + 1, max(up) - min(up) + 1))
        return sizes

    @classmethod
    def from_matches(
        cls,
        hallway: Hallway,
        closed_endpoint: Endpoint,
        closed_blocks: World,
        seal: Seal,
        patterns: tuple[PatternMatch, ...],
    ) -> DoorCandidate:
        """Summarize which coordinates a reviewer needs to distinguish.

        Two interpretations can agree on a front panel but disagree about
        the second layer. A block belongs to the agreed set if every match
        uses it. A block used by only some matches is disputed. If no known pattern
        matches, retain all the observed blocks for manual review.
        """
        selections = [set(pattern.blocks) for pattern in patterns]
        if selections:
            agreed = set.intersection(*selections)
            disputed = set.union(*selections) - agreed
        else:
            agreed, disputed = set(), set(closed_blocks)
        return cls(
            axis=hallway.axis,
            closed_endpoint=closed_endpoint,
            seal=seal,
            interior=tuple(sorted(hallway.interior)),
            closed_blocks=tuple(ObservedBlock(p, state) for p, state in sorted(closed_blocks.items())),
            patterns=patterns,
            agreed_blocks=tuple(sorted(agreed)),
            disputed_blocks=tuple(sorted(disputed)),
        )


@dataclass(frozen=True, slots=True)
class DoorDetection:
    """All surviving interpretations, or a reason when the detector has no conclusion."""

    candidates: tuple[DoorCandidate, ...] = ()
    reason: str = ""


def detect_cycle(initial: World, actuated: World, returned: World, full_blocks: Set[str]) -> DoorDetection:
    """Locate a passage in either polarity of a two-use cycle.

    All three sparse maps use schematic coordinates and omit air. Requiring the
    original state to return inside the hallway discards one-shot motion
    while allowing unrelated mechanisms elsewhere to change.

    First measure where the open tunnel runs. Then inventory all closed blocks
    inside it and ask two independent questions. Can air still cross the tunnel?
    Which known patterns could account for these blocks? A full seal answers
    the first question even if the second has several answers or none.
    """
    positions = initial.keys() | actuated.keys() | returned.keys()
    if not positions:
        return DoorDetection(reason="Empty cycle.")
    bounds = tuple(max(p[a] for p in positions) - min(p[a] for p in positions) + 1 for a in range(3))
    if prod(bounds) > MAX_WORLD_VOLUME:
        return DoorDetection(reason="Cycle exceeds the hallway search volume.")
    changes = {p for p in positions if initial.get(p) != actuated.get(p)}
    candidates: list[DoorCandidate] = []
    # "Initial" only names the time of the snapshot. The file may have been
    # saved with the plug in place, so either snapshot can contain the tunnel.
    endpoints: tuple[tuple[World, World, Endpoint], ...] = (
        (initial, actuated, "actuated"),
        (actuated, initial, "initial"),
    )
    remaining_pattern_cells = MAX_WORLD_VOLUME
    for opened, closed, endpoint in endpoints:
        for axis in AXES:
            for hall in hallways(opened, full_blocks, axis, changes):
                if any(initial.get(p) != returned.get(p) for p in hall.interior):
                    continue
                observed = {p: closed[p] for p in hall.interior if p in closed}
                if changes.isdisjoint(observed):
                    continue
                seal = hall.seal(observed, full_blocks)
                # Ambiguous layered plugs can produce many placements. Return
                # an inconclusive result on overflow. A truncated list would
                # incorrectly imply agreement among the surviving matches.
                supported = []
                for match in matches(hall, observed, opened):
                    remaining_pattern_cells -= len(match.blocks)
                    if remaining_pattern_cells < 0:
                        return DoorDetection(reason="Too many possible pattern placements to compare.")
                    supported.append(match)
                patterns = tuple(supported)
                candidates.append(DoorCandidate.from_matches(hall, endpoint, observed, seal, patterns))
    return DoorDetection(tuple(candidates), "" if candidates else "No repeatable straight hallway found.")
