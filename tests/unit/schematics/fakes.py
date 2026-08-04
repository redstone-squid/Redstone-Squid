"""Test doubles for the schematic ports.

Kept free of the native engine so the whole unit suite runs on a machine without the optional
extra installed, which is the deployment the null analyzer exists to support.
"""

from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicLimits,
    SchematicMetrics,
    SchematicSign,
    SimulationResult,
    VersionLossEntry,
)
from squid.schematics.errors import InvalidSchematicError


def make_analysis(
    *,
    sha256: str = "0" * 64,
    dimensions: tuple[int, int, int] = (3, 4, 5),
    block_count: int = 42,
    structural: str = "structural-hash",
    shape: str = "shape-hash",
    exact: str = "exact-hash",
    analyzer_version: str = "nucleation-test",
    lattice: AutostackLattice | None = None,
    signs: tuple[SchematicSign, ...] = (),
) -> SchematicAnalysis:
    """Build a plausible analysis without going anywhere near the engine."""
    width, height, length = dimensions
    return SchematicAnalysis(
        metrics=SchematicMetrics(
            source_format=SchematicFormat.LITEMATIC,
            byte_size=256,
            sha256=sha256,
            dimensions=SchematicDimensions(width, height, length),
            allocated_dimensions=SchematicDimensions(width, height, length),
            block_count=block_count,
            bounding_volume=width * height * length,
            entity_count=0,
            palette_size=3,
            region_names=("Main",),
            signs=signs,
        ),
        fingerprints=SchematicFingerprints(structural=structural, shape=shape, exact=exact),
        analyzer_version=analyzer_version,
        analysis_schema_version=1,
        lattice=lattice,
    )


class FakeSchematicAnalyzer:
    """Return a canned analysis, and record what it was asked to do."""

    def __init__(self, analysis: SchematicAnalysis | None = None, *, failure: Exception | None = None) -> None:
        self.analysis = analysis or make_analysis()
        self.failure = failure
        self.analyze_calls: list[tuple[bytes, SchematicFormat | None, bool]] = []
        self.convert_calls: list[tuple[SchematicFormat, int | None]] = []
        self.compare_calls: list[tuple[bytes, bytes, FingerprintPreset, float | None]] = []
        self.comparisons: dict[bytes, SchematicComparison] = {}
        self.closed = False

    async def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(available=True, analyzer_version=self.analysis.analyzer_version)

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        self.analyze_calls.append((data, source_format, with_lattice))
        if self.failure is not None:
            raise self.failure
        return self.analysis

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        self.convert_calls.append((target, data_version))
        return b"converted", ()

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        self.compare_calls.append((left, right, preset, timeout_seconds))
        return self.comparisons.get(
            right,
            SchematicComparison(preset=preset, identical=left == right, footprint_distance=0.0),
        )

    async def render(self, data: bytes, *, request: object, resource_pack: bytes | None = None) -> bytes:
        raise InvalidSchematicError

    async def simulate(self, data: bytes, *, request: object) -> SimulationResult:
        raise InvalidSchematicError

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        return b"stacked"

    async def aclose(self) -> None:
        self.closed = True


class FakeSchematicStore:
    """An in-memory stand-in for the schematic repository."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.records: list[tuple[int, str, SchematicAnalysis, bool]] = []
        self.stored: list[StoredSchematic] = []

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        self.files[digest] = data
        return digest

    async def get_file(self, sha256: str) -> bytes | None:
        return self.files.get(sha256)

    async def record_analysis(
        self,
        build_id: int,
        sha256: str,
        analysis: SchematicAnalysis,
        *,
        primary: bool,
        original_filename: str | None = None,
        uploaded_by_discord_id: int | None = None,
    ) -> int:
        self.records.append((build_id, sha256, analysis, primary))
        stored = StoredSchematic(
            id=len(self.records),
            build_id=build_id,
            file_sha256=sha256,
            is_primary=primary,
            original_filename=original_filename,
            analysis=analysis,
        )
        self.stored.append(stored)
        return stored.id

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        return [stored for stored in self.stored if stored.build_id == build_id]

    async def get_primary(self, build_id: int) -> StoredSchematic | None:
        return next((s for s in self.stored if s.build_id == build_id and s.is_primary), None)

    async def find_file_matches(
        self,
        sha256: str,
        *,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        return [
            stored for stored in self.stored if stored.file_sha256 == sha256 and stored.build_id != exclude_build_id
        ][:limit]

    async def find_fingerprint_matches(
        self,
        fingerprint: str,
        *,
        preset: FingerprintPreset,
        analyzer_version: str,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        attribute = {
            FingerprintPreset.STRUCTURAL: "structural",
            FingerprintPreset.SHAPE: "shape",
            FingerprintPreset.EXACT: "exact",
        }[preset]
        return [
            stored
            for stored in self.stored
            if getattr(stored.analysis.fingerprints, attribute) == fingerprint
            and stored.analysis.analyzer_version == analyzer_version
            and stored.build_id != exclude_build_id
        ][:limit]

    async def find_metric_neighbours(
        self,
        metrics: SchematicMetrics,
        *,
        tolerance: float,
        limit: int = 25,
        exclude_build_id: int | None = None,
    ) -> list[StoredSchematic]:
        low = int(metrics.block_count * (1 - tolerance))
        high = int(metrics.block_count * (1 + tolerance)) + 1
        return [
            stored
            for stored in self.stored
            if low <= stored.analysis.metrics.block_count <= high and stored.build_id != exclude_build_id
        ][:limit]

    async def record_render(self, schematic_id: int, recipe_hash: str, url: str) -> None:
        return None


class FakeVersionResolver:
    """Resolve exactly one version, so tests can exercise both branches."""

    def __init__(self, known: dict[str, int] | None = None) -> None:
        self.known = known if known is not None else {"Java 1.16.5": 2586}

    async def data_version_for(self, version_label: str) -> int | None:
        return self.known.get(version_label)

    async def label_for_data_version(self, data_version: int) -> str | None:
        return next((label for label, value in self.known.items() if value == data_version), None)
