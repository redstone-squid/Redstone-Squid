"""Duplicate-detection read models."""

from dataclasses import dataclass
from typing import Literal

type DuplicateTier = Literal["identical", "structural-match", "near"]


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A previously submitted build whose schematic resembles the one under review."""

    build_id: int
    schematic_id: int
    tier: DuplicateTier
    footprint_distance: float
    detail: str | None = None
