"""Schematic domain values.

Every type here is frozen, slotted, and built from standard-library types only. Results from
the native engine are translated into these values inside the infrastructure adapter, so no
engine type ever crosses into the application or transport layers.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

type Vector3 = tuple[int, int, int]

SCHEMATIC_FILE_SCHEMA_MAX_BYTES = 16 * 1024 * 1024
"""Fixed compressed-byte ceiling encoded in the database schema."""


class SchematicFormat(StrEnum):
    """A schematic container format this application can read."""

    LITEMATIC = "litematic"
    SPONGE_SCHEM = "schem"
    LEGACY_SCHEMATIC = "schematic"
    STRUCTURE_NBT = "nbt"
    MCSTRUCTURE = "mcstructure"


class FingerprintPreset(StrEnum):
    """A structural-equivalence preset understood by the fingerprint and diff engines.

    `STRUCTURAL` and `SHAPE` are translation-invariant, so two copies of one build placed at
    different coordinates share a fingerprint. `SHAPE` is additionally orientation-blind.
    `EXACT` is material- and orientation-sensitive.
    """

    STRUCTURAL = "structural"
    SHAPE = "shape"
    EXACT = "exact"


class SchematicVisibility(StrEnum):
    """Explicit publication choice for one build attachment."""

    LEGACY_UNVERIFIED = "legacy_unverified"
    REVIEWER_ONLY = "reviewer_only"
    PUBLIC_DOWNLOAD = "public_download"


class SchematicLicense(StrEnum):
    """Licenses a submitter may grant for a public schematic download."""

    CC0_1_0 = "cc0_1_0"
    CC_BY_4_0 = "cc_by_4_0"
    CC_BY_SA_4_0 = "cc_by_sa_4_0"
    CC_BY_ND_4_0 = "cc_by_nd_4_0"
    CC_BY_NC_4_0 = "cc_by_nc_4_0"
    CC_BY_NC_SA_4_0 = "cc_by_nc_sa_4_0"
    CC_BY_NC_ND_4_0 = "cc_by_nc_nd_4_0"

    @property
    def uri(self) -> str:
        """Return the canonical Creative Commons deed URI."""
        code = self.value.replace("_", "-")
        if self is SchematicLicense.CC0_1_0:
            return "https://creativecommons.org/publicdomain/zero/1.0/"
        family, major, minor = code.removeprefix("cc-").rsplit("-", 2)
        return f"https://creativecommons.org/licenses/{family}/{major}.{minor}/"


@dataclass(frozen=True, slots=True)
class SchematicLimits:
    """Resource budgets applied to attacker-controlled schematic uploads.

    Enforced at three points: `max_upload_bytes` before the file is downloaded from Discord,
    `max_inflated_bytes` while streaming decompression, and `max_allocated_volume` in the
    worker immediately after the engine loads the file.
    """

    max_upload_bytes: int = SCHEMATIC_FILE_SCHEMA_MAX_BYTES
    max_inflated_bytes: int = 64 * 1024 * 1024
    max_allocated_volume: int = 20_000_000
    max_axis_length: int = 512
    max_sniff_bytes: int = 64 * 1024


@dataclass(frozen=True, slots=True)
class SchematicDimensions:
    """A schematic's extent along each axis."""

    width: int
    height: int
    length: int

    @property
    def volume(self) -> int:
        """Return the bounding-box volume, air included."""
        return self.width * self.height * self.length


@dataclass(frozen=True, slots=True)
class SchematicSign:
    """Text recovered from a sign placed inside a schematic."""

    x: int
    y: int
    z: int
    text: str


@dataclass(frozen=True, slots=True)
class SchematicFingerprints:
    """Canonical structural hashes of one schematic.

    Only comparable against fingerprints produced by the same analyzer version; see
    :attr:`SchematicAnalysis.analyzer_version`.
    """

    structural: str
    shape: str
    exact: str
    signature_structural: str | None = None


@dataclass(frozen=True, slots=True)
class AutostackLattice:
    """A repeating structure detected inside a build.

    `vectors` holds one period vector for a 1D run and two for a 2D array. `coverage` is the
    fraction of the build explained by this period, so a decoder attached to an otherwise
    periodic screen still yields a usable lattice.
    """

    mode: Literal["1d", "2d"]
    vectors: tuple[Vector3, ...]
    coverage: float
    cell_min: Vector3
    cell_max: Vector3
    region_min: Vector3
    region_max: Vector3
    label: str | None = None


@dataclass(frozen=True, slots=True)
class VersionLossEntry:
    """One fidelity loss reported when converting between Minecraft data versions."""

    version: str
    kind: str
    severity: Literal["Loss", "Approximated"]
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class SchematicMetrics:
    """Authoritative facts read out of one schematic file.

    `block_count` is the number of non-air blocks. It is **not** the Door Rules cumulative
    volume, which includes air pockets and carries hallway, frame, and hitbox exceptions;
    keep the two separate when presenting record evidence.
    """

    source_format: SchematicFormat
    byte_size: int
    sha256: str
    dimensions: SchematicDimensions
    allocated_dimensions: SchematicDimensions
    block_count: int
    bounding_volume: int
    entity_count: int
    palette_size: int
    region_names: tuple[str, ...] = ()
    source_data_version: int | None = None
    declared_name: str | None = None
    declared_author: str | None = None
    signs: tuple[SchematicSign, ...] = ()


@dataclass(frozen=True, slots=True)
class SchematicAnalysis:
    """Everything one pass over a schematic file produced.

    `analyzer_version` and `analysis_schema_version` are load-bearing: fingerprints are not
    stable across engine upgrades, so persisted fingerprints record what produced them and
    duplicate lookups filter on it. A version bump therefore becomes a backfill job instead
    of a silent correctness regression.
    """

    metrics: SchematicMetrics
    fingerprints: SchematicFingerprints
    analyzer_version: str
    analysis_schema_version: int
    lattice: AutostackLattice | None = None


@dataclass(frozen=True, slots=True)
class SchematicComparison:
    """The result of comparing two schematics under one preset."""

    preset: FingerprintPreset
    identical: bool
    footprint_distance: float
    edit_distance: int | None = None
    support: float | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationSample:
    """The observed state of one watched position after a given tick."""

    tick: int
    x: int
    y: int
    z: int
    powered: bool
    signal_strength: int


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Piston-door timing evidence from the vanilla-accurate tick engine.

    This is moderator-facing evidence, never a record value: simulated timings must not
    populate a build's declared opening or closing time.
    """

    ticks_run: int
    settled_tick: int | None
    input_position: Vector3 | None = None
    input_source: Literal["insign", "heuristic", "manual"] | None = None
    last_piston_tick: int | None = None
    block_changes: int = 0
    piston_events: int = 0
    redstone_events: int = 0
    trustworthy: bool = False
    samples: tuple[SimulationSample, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalyzerCapabilities:
    """What the schematic engine on this instance can actually do."""

    available: bool
    analyzer_version: str | None = None
    can_render: bool = False
    can_simulate: bool = False
    render_backends: tuple[str, ...] = field(default_factory=tuple)
    unavailable_reason: str | None = None
