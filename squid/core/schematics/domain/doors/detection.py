"""Compare repeatable endpoints of an input cycle in source coordinates."""

from collections.abc import Set
from dataclasses import dataclass
from math import prod
from typing import Literal

from squid.core.schematics.domain.doors.hallway import AXES, World, hallways
from squid.core.schematics.domain.doors.patterns import PatternMatch, matches
from squid.core.schematics.domain.models import Vector3

MAX_WORLD_VOLUME = 250_000


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
    closed_endpoint: Literal["initial", "actuated"]
    seal: Literal["sealed", "passage_remains", "unknown"]
    interior: tuple[Vector3, ...]
    closed_blocks: tuple[ObservedBlock, ...]
    patterns: tuple[PatternMatch, ...]
    agreed_blocks: tuple[Vector3, ...]
    disputed_blocks: tuple[Vector3, ...]

    @property
    def opening_size(self) -> tuple[int, int]:
        u, v, _ = AXES[self.axis]
        width = max(p[u] for p in self.interior) - min(p[u] for p in self.interior) + 1
        height = max(p[v] for p in self.interior) - min(p[v] for p in self.interior) + 1
        return width, height


@dataclass(frozen=True, slots=True)
class DoorDetection:
    candidates: tuple[DoorCandidate, ...] = ()
    reason: str = ""


def detect_cycle(initial: World, actuated: World, returned: World, full_blocks: Set[str]) -> DoorDetection:
    """Locate a passage in either polarity of a two-use cycle.

    All three sparse maps use schematic coordinates and omit air. Requiring the
    original state to return inside the candidate discards one-shot motion
    while allowing unrelated mechanisms elsewhere to change. A partially
    open pattern remains a candidate even when the two mouths stay connected.
    """
    positions = initial.keys() | actuated.keys() | returned.keys()
    if not positions:
        return DoorDetection(reason="Empty cycle.")
    bounds = tuple(max(p[a] for p in positions) - min(p[a] for p in positions) + 1 for a in range(3))
    if prod(bounds) > MAX_WORLD_VOLUME:
        return DoorDetection(reason="Cycle exceeds the hallway search volume.")
    changes = {p for p in positions if initial.get(p) != actuated.get(p)}
    candidates: list[DoorCandidate] = []
    endpoints: tuple[tuple[World, World, Literal["initial", "actuated"]], ...] = (
        (initial, actuated, "actuated"),
        (actuated, initial, "initial"),
    )
    for opened, closed, endpoint in endpoints:
        for axis in range(3):
            for hall in hallways(opened, full_blocks, axis, changes):
                if any(initial.get(p) != returned.get(p) for p in hall.interior):
                    continue
                observed = {p: closed[p] for p in hall.interior if p in closed}
                if not any(p in changes for p in observed) or not hall.connects(opened.keys()):
                    continue
                seal: Literal["sealed", "passage_remains", "unknown"]
                # Bound connectivity from both sides. If even allowing partial
                # blocks through cannot connect the mouths, full cubes prove a
                # seal. A path of air proves an opening. Slabs and other partial
                # shapes can leave the answer between these two cases.
                if not hall.connects({p for p, state in observed.items() if state in full_blocks}):
                    seal = "sealed"
                elif hall.connects(observed.keys()):
                    seal = "passage_remains"
                else:
                    seal = "unknown"
                patterns = tuple(matches(hall, observed, opened))
                choices = [set(pattern.blocks) for pattern in patterns]
                agreed = set.intersection(*choices) if choices else set()
                disputed = set.union(*choices) - agreed if choices else set(observed)
                candidates.append(
                    DoorCandidate(
                        axis,
                        endpoint,
                        seal,
                        tuple(sorted(hall.interior)),
                        tuple(ObservedBlock(p, state) for p, state in sorted(observed.items())),
                        patterns,
                        tuple(sorted(agreed)),
                        tuple(sorted(disputed)),
                    )
                )
    return DoorDetection(tuple(candidates), "" if candidates else "No repeatable straight hallway found.")
