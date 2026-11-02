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
    """Native schematic operations, executed off the event loop.

    Implementations take bytes and return domain values. They deliberately do not accept or
    return engine handles: a schematic object reused across calls would carry a cached
    simulation world with it, so every operation reloads from bytes.
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

        `source_format` is what the caller's own content sniff concluded, filename hint
        included. Implementations sniff the bytes themselves when it is omitted, but they
        cannot see the filename, so passing it through gives a better answer for formats whose
        root compounds are ambiguous.
        """
        ...

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]: ...

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

    async def render(self, data: bytes, *, request: RenderRequest, resource_pack: bytes | None = None) -> bytes: ...

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult: ...

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes: ...

    async def capabilities(self) -> AnalyzerCapabilities: ...

    async def aclose(self) -> None:
        """Release any process-level resources this analyzer owns."""
        ...


class SchematicStore(Protocol):
    """Persistence operations required by the schematic service."""

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        """Store bytes content-addressed by SHA-256 and return the digest."""
        ...

    async def get_file(self, sha256: str) -> bytes | None: ...

    async def record_analysis(
        self,
        build_id: int,
        sha256: str,
        analysis: SchematicAnalysis,
        *,
        primary: bool,
        original_filename: str | None = None,
        uploaded_by_discord_id: int | None = None,
        publication: SchematicPublication | None = None,
    ) -> int: ...

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]: ...

    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None: ...

    async def get_primary(self, build_id: int) -> StoredSchematic | None: ...

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

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None: ...

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
        """Record and project a render only while its schematic remains primary."""
        ...

    async def project_render(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Project a cached render only while its schematic remains primary."""
        ...

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None: ...

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        """Persist moderator-facing simulation evidence for one attachment."""
        ...


class SchematicResourcePackProvider(Protocol):
    """Lazy source for verified resource-pack bytes and their SHA-256 digest."""

    async def load(self) -> tuple[bytes, str]: ...

    async def aclose(self) -> None: ...


class SchematicVersionResolver(Protocol):
    """Translation between Minecraft version labels and numeric data versions."""

    async def data_version_for(self, version_label: str) -> int | None: ...

    async def label_for_data_version(self, data_version: int) -> str | None: ...
