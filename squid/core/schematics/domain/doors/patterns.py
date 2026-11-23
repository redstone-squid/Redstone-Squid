"""Match closed block arrangements in hallway-local coordinates."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from squid.core.schematics.domain.doors.hallway import AXES, Hallway
from squid.core.schematics.domain.models import Vector3


@dataclass(frozen=True, slots=True)
class PatternMatch:
    arrangement: str
    depth: int
    facing: int
    compositions: tuple[str, ...]
    blocks: tuple[Vector3, ...]


def compositions(observed: Mapping[Vector3, str], blocks: list[Vector3], u: int, v: int) -> tuple[str, ...]:
    """Describe the materials of one match, excluding the surrounding mechanism."""
    materials = {observed[p].partition("[")[0] for p in blocks}
    if materials == {"minecraft:glass"}:
        return ("glass",)
    if len(materials) == 2:
        # Checkerboard parity survives translations and reflections of the frame.
        by_parity = [{observed[p].partition("[")[0] for p in blocks if (p[u] + p[v]) % 2 == i} for i in (0, 1)]
        if all(len(group) == 1 for group in by_parity):
            return ("checkerboard",)
    return ()


def matches(hallway: Hallway, observed: Mapping[Vector3, str], opened: Mapping[Vector3, str]) -> Iterator[PatternMatch]:
    """Generate supported placements and test them against both endpoints.

    Try each occupied depth as the front of the arrangement. A flat panel uses
    the hallway outline at a single depth. On a rectangular funnel each ring
    steps one block along the travel axis for every block inward from the rim.
    Trying both signs handles either facing without rotating the world.

    A match requires each proposed door cell to be occupied when closed and
    clear when open. A bar also requires the flanking cells in its plane to
    be clear. Deeper blocks do not disqualify a shallow match. If another
    placement explains those blocks too, both interpretations survive.
    """
    u, v, axis = AXES[hallway.axis]
    profile = {(p[u], p[v]) for p in hallway.interior}
    left, right = min(x for x, _ in profile), max(x for x, _ in profile)
    bottom, top = min(y for _, y in profile), max(y for _, y in profile)
    rectangular = len(profile) == (right - left + 1) * (top - bottom + 1)
    # The same outline can carry a flat sheet, stepped rings, or a central
    # bar. Unspecified positions outside the generated pattern may be occupied.
    arrangements = [("flat", 0)]
    if rectangular and min(right - left, top - bottom) >= 2:
        arrangements.extend((("stepped_funnel", 1), ("stepped_funnel", -1)))
    if right - left >= 2 and (right - left) % 2 == 0:
        arrangements.append(("center_bar", 0))
    if top - bottom >= 2 and (top - bottom) % 2 == 0:
        arrangements.append(("horizontal_bar", 0))
    for depth in sorted({p[axis] for p in observed}):
        for name, facing in arrangements:
            required: list[Vector3] = []
            for x, y in profile:
                ring = min(x - left, right - x, y - bottom, top - y)
                local = [0, 0, 0]
                local[u], local[v], local[axis] = x, y, depth + facing * ring
                p = local[0], local[1], local[2]
                if p not in hallway.interior:
                    break
                if (name == "center_bar" and x != (left + right) // 2) or (
                    name == "horizontal_bar" and y != (bottom + top) // 2
                ):
                    if p in observed:  # This pattern requires clear cells beside the bar.
                        break
                elif p in observed and p not in opened:
                    required.append(p)
                else:
                    break
            else:
                if required:
                    yield PatternMatch(
                        name, depth, facing, compositions(observed, required, u, v), tuple(sorted(required))
                    )
