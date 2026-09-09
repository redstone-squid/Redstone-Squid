"""Application ports for schematic analysis and storage."""

from typing import Protocol

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.queries import SchematicPublication, StoredRender, StoredSchematic
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicFormat,
    SchematicLimits,
    SchematicMetrics,
    SimulationResult,
    VersionLossEntry,
)


class SchematicAnalyzer(Protocol):
    """Native schematic operations, executed off the event loop; `aclose` ends the analyzer for good.

    Implementations take bytes and return domain values, never engine handles: a schematic object
    reused across calls carries a cached simulation world with it, so every operation reloads from
    bytes. Any operation may raise `InvalidSchematicError`, `SchematicTooLargeError`,
    `SchematicTimeoutError`, `SchematicWorkerCrashedError`, or `SchematicSupportUnavailableError`.
    """

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        """Read every fact worth persisting about one schematic file.

        `source_format` is what the caller's own content sniff concluded, filename hint included.
        Implementations sniff the bytes again when it is omitted, but they cannot see the filename,
        so passing it through gives a better answer for ambiguous root compounds.
        """
        ...

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        """Re-encode into `target`, retargeting `data_version` when given; returns bytes and fidelity losses."""
        ...

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        """Compare two files, optionally under a stricter caller-owned deadline."""
        ...

    async def render(self, data: bytes, *, request: RenderRequest, resource_pack: bytes | None = None) -> bytes:
        """Render to PNG bytes with `resource_pack`; implementations that own a GPU serialise renders."""
        ...

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        """Actuate one input and return tick-engine timing evidence.

        Raises `AmbiguousSimulationInputError` when the schematic offers no single control to press.
        """
        ...

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        """Repeat `lattice`'s unit cell `counts` times along its period vectors and re-encode."""
        ...

    async def capabilities(self) -> AnalyzerCapabilities:
        """What this engine build can do; an unusable engine answers `available=False` rather than raising."""
        ...

    async def aclose(self) -> None:
        """Release any process-level resources this analyzer owns; no operation may follow."""
        ...


class SchematicStore(Protocol):
    """Persistence operations required by the schematic service."""

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        """Store bytes content-addressed by SHA-256 and return the digest. Idempotent."""
        ...

    async def get_file(self, sha256: str) -> bytes | None:
        """The stored bytes, or `None` when no file row carries that digest."""
        ...

    async def record_analysis(
        self,
        build_id: int,
        sha256: str,
        analysis: SchematicAnalysis,
        *,
        primary: bool,
        original_filename: str | None = None,
        uploaded_by_account_id: int | None = None,
        publication: SchematicPublication | None = None,
    ) -> int:
        """Attach `analysis` to a build, replacing any earlier analysis of the same file.

        `primary=True` demotes the build's previous primary and re-enqueues its render, since the
        build's preview now describes the wrong file. Returns the attachment id.
        """
        ...

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        """Every attachment of the build, published or not."""
        ...

    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None:
        """One attachment, or `None` when it does not belong to `build_id`."""
        ...

    async def get_primary(self, build_id: int) -> StoredSchematic | None:
        """The build's primary attachment, or `None` when it has none."""
        ...

    async def find_file_matches(
        self,
        sha256: str,
        *,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        """Return builds attached to exactly the same uploaded bytes."""
        ...

    async def find_fingerprint_matches(
        self,
        fingerprint: str,
        *,
        preset: FingerprintPreset,
        analyzer_version: str,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        """Return schematics sharing a fingerprint, restricted to one analyzer version.

        Fingerprints are only comparable within the version that produced them, so callers
        must never widen this beyond a single `analyzer_version`.
        """
        ...

    async def find_metric_neighbours(
        self,
        metrics: SchematicMetrics,
        *,
        tolerance: float,
        limit: int = 25,
        exclude_build_id: int | None = None,
    ) -> list[StoredSchematic]:
        """Shortlist schematics of comparable size for pairwise near-duplicate ranking."""
        ...

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None:
        """The stored render for one recipe, or `None` when that recipe has never been rendered."""
        ...

    async def record_render(
        self,
        schematic_id: int,
        recipe_hash: str,
        url: str,
        object_key: str,
        *,
        width: int,
        height: int,
        byte_size: int,
    ) -> StoredRender | None:
        """Record and project a render only while its schematic remains primary; `None` otherwise."""
        ...

    async def project_render(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Project an already-recorded render only while its schematic remains primary."""
        ...

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None:
        """The stored PNG for a recipe, or `None` when it is unknown, unstored, or over `max_bytes`."""
        ...

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        """Persist moderator-facing simulation evidence for one attachment."""
        ...


class SchematicResourcePackProvider(Protocol):
    """Lazy source for verified resource-pack bytes; `aclose` ends the provider and its transport.

    The digest is part of every render's recipe hash, so a pack swap invalidates cached previews.
    """

    async def load(self) -> tuple[bytes, str]:
        """The verified pack and its SHA-256, fetched on first use.

        Raises `SchematicRenderUnavailableError` when no pack is configured, or when the configured
        one is unreadable, oversized, or fails its digest check.
        """
        ...

    async def aclose(self) -> None:
        """Release whatever transport this provider opened to fetch the pack."""
        ...


class SchematicVersionResolver(Protocol):
    """Translation between Minecraft version labels and numeric data versions."""

    async def data_version_for(self, version_label: str) -> int | None:
        """The data version for a label, or `None` when the label is unknown or unparseable."""
        ...

    async def label_for_data_version(self, data_version: int) -> str | None:
        """The earliest release label carrying `data_version`, or `None` when no version does."""
        ...
