"""Application ports for schematic analysis and storage."""

from typing import Protocol

from whenever import Instant

from squid.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.previews import PreviewObjectReservation, StoredRender
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
from squid.schematics.domain.values import VerifiedResourcePack


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

    async def render(
        self, data: bytes, *, request: RenderRequest, resource_pack: VerifiedResourcePack | None = None
    ) -> bytes: ...

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
        uploaded_by_account_id: int | None = None,
        publication: SchematicPublication | None = None,
    ) -> int: ...

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]: ...

    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None: ...

    async def get_featured(self, build_id: int) -> StoredSchematic | None:
        """Return the attachment selected to supply a build's generated preview."""
        ...

    async def find_file_matches(
        self,
        sha256: str,
        *,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        """Return builds attached to exactly the same uploaded bytes.

        `exclude_build_id` omits only the build whose submission is currently being edited.
        """
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
        `exclude_build_id` omits only the build whose submission is currently being edited.
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
        """Shortlist schematics of comparable size for pairwise near-duplicate ranking.

        `exclude_build_id` omits only the build whose submission is currently being edited.
        """
        ...

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        """Persist moderator-facing simulation evidence for one attachment."""
        ...


class SchematicPreviewPublisher(Protocol):
    """Persistence operations for generated preview recipes and publication."""

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None: ...

    async def reserve_preview_object(
        self,
        object_key: str,
        *,
        byte_size: int,
        sha256: str,
    ) -> PreviewObjectReservation: ...

    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None: ...

    async def publish_fresh_preview(
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
        """Record and publish a generated preview only while its source remains featured."""
        ...

    async def publish_cached_preview(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Publish a cached generated preview only while its source remains featured."""
        ...

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None: ...

    async def cleanup_unreferenced_preview_objects(self, *, older_than: Instant, limit: int) -> int: ...


class SchematicResourcePackProvider(Protocol):
    """Lazy source for digest-verified resource-pack bytes and media metadata."""

    async def load(self) -> VerifiedResourcePack: ...

    async def aclose(self) -> None: ...


class SchematicVersionResolver(Protocol):
    """Translation between Minecraft version labels and numeric data versions."""

    async def data_version_for(self, version_label: str) -> int | None: ...

    async def label_for_data_version(self, data_version: int) -> str | None: ...
