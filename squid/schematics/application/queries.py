"""Read models returned by the schematic store."""

from dataclasses import dataclass
from typing import Literal

from squid.schematics.domain.models import SchematicAnalysis

type DuplicateTier = Literal["identical", "structural-match", "near"]


@dataclass(frozen=True, slots=True)
class StoredSchematic:
    """A schematic file that has been analyzed and attached to a build."""

    id: int
    build_id: int
    file_sha256: str
    is_primary: bool
    original_filename: str | None
    analysis: SchematicAnalysis


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A previously submitted build whose schematic resembles the one under review."""

    build_id: int
    schematic_id: int
    tier: DuplicateTier
    footprint_distance: float
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class StoredRender:
    """A persisted, recipe-keyed rendered preview."""

    schematic_id: int
    recipe_hash: str
    url: str
    width: int
    height: int
    byte_size: int


@dataclass(frozen=True, slots=True)
class PreparedRender:
    """A cached URL or fresh PNG awaiting upload by the transport layer."""

    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    png: bytes | None = None
    cached_url: str | None = None

    def __post_init__(self) -> None:
        if (self.png is None) == (self.cached_url is None):
            msg = "A prepared render must contain exactly one of png and cached_url."
            raise ValueError(msg)
