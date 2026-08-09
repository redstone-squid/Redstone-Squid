"""Schematic application services."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from squid.core.errors import SquidError
from squid.schematics.application.commands import ConvertRequest, IngestRequest, RenderRequest, SimulationRequest
from squid.schematics.application.ports import (
    SchematicAnalyzer,
    SchematicResourcePackProvider,
    SchematicStore,
    SchematicVersionResolver,
)
from squid.schematics.application.queries import (
    DuplicateCandidate,
    DuplicateTier,
    PreparedRender,
    StoredRender,
    StoredSchematic,
)
from squid.schematics.domain.formats import inflated_size_at_most, sniff_schematic_format
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicFormat,
    SchematicLimits,
    SimulationResult,
    Vector3,
    VersionLossEntry,
)
from squid.schematics.errors import (
    InvalidSchematicError,
    SchematicNotFoundError,
    SchematicSupportUnavailableError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestedSchematic:
    """The result of accepting one upload: its digest and what the engine read from it."""

    sha256: str
    analysis: SchematicAnalysis


class SchematicService:
    """Accept, analyze, store, and re-encode schematic files.

    The service owns the order in which an upload is vetted, and that order is the security
    property: cheap stdlib checks run to exhaustion on the caller's thread before a single byte
    is handed to the native engine, and the engine only ever sees bytes that have already been
    proven to be a bounded, well-formed NBT stream.
    """

    def __init__(
        self,
        analyzer: SchematicAnalyzer,
        store: SchematicStore,
        versions: SchematicVersionResolver,
        *,
        limits: SchematicLimits | None = None,
        engine_installed: bool = True,
        duplicate_metric_tolerance: float = 0.2,
        duplicate_near_distance: float = 1.0,
        duplicate_max_comparisons: int = 5,
        duplicate_result_limit: int = 3,
        duplicate_total_timeout_seconds: float = 15.0,
        render_enabled: bool = False,
        resource_pack: SchematicResourcePackProvider | None = None,
        render_request: RenderRequest | None = None,
        render_max_block_count: int = 400_000,
        render_max_bounding_volume: int = 2_000_000,
    ) -> None:
        self._analyzer = analyzer
        self._store = store
        self._versions = versions
        self._limits = limits or SchematicLimits()
        self._available = engine_installed
        self._duplicate_metric_tolerance = duplicate_metric_tolerance
        self._duplicate_near_distance = duplicate_near_distance
        self._duplicate_max_comparisons = duplicate_max_comparisons
        self._duplicate_result_limit = duplicate_result_limit
        self._duplicate_total_timeout_seconds = duplicate_total_timeout_seconds
        self._render_enabled = render_enabled
        self._resource_pack = resource_pack
        self._render_request = render_request or RenderRequest()
        self._render_max_block_count = render_max_block_count
        self._render_max_bounding_volume = render_max_bounding_volume
        self._render_attempted: set[tuple[int, str]] = set()
        self._render_warning_emitted = False
        self._poisoned: set[str] = set()
        """Digests of files that killed a worker. Re-analysing one would kill the next worker
        too, so this process refuses them outright rather than retrying."""

    @property
    def available(self) -> bool:
        """Whether schematic features should be offered at all on this instance."""
        return self._available

    @property
    def limits(self) -> SchematicLimits:
        """The budgets callers must apply before reading an attachment."""
        return self._limits

    async def capabilities(self) -> AnalyzerCapabilities:
        if not self._available:
            return AnalyzerCapabilities(available=False, unavailable_reason="The schematic engine is not installed.")
        return await self._analyzer.capabilities()

    async def ingest(self, request: IngestRequest) -> IngestedSchematic:
        """Vet, store, and analyze one uploaded file.

        Storing happens *before* analysis so that a file which crashes the engine is still on
        disk to reproduce with, and so a byte-identical resubmission is recognised without
        paying for analysis twice.
        """
        self._require_available()
        source_format = self._vet(request.data, filename=request.filename)
        sha256 = await self._store.put_file(request.data, source_format=source_format)

        if sha256 in self._poisoned:
            msg = "This file has already crashed the schematic engine on this instance."
            raise InvalidSchematicError(
                msg,
                context={"sha256": sha256},
                end_user_action="Re-export the schematic and try again.",
            )

        try:
            analysis = await self._analyzer.analyze(
                request.data,
                limits=self._limits,
                with_lattice=request.with_lattice,
                source_format=source_format,
            )
        except SchematicWorkerCrashedError:
            self._poisoned.add(sha256)
            raise
        return IngestedSchematic(sha256, analysis)

    async def attach(self, build_id: int, request: IngestRequest, *, primary: bool = True) -> IngestedSchematic:
        """Ingest a file and record it against a build that already exists."""
        ingested = await self.ingest(request)
        await self.record(build_id, ingested, request, primary=primary)
        return ingested

    async def record(
        self, build_id: int, ingested: IngestedSchematic, request: IngestRequest, *, primary: bool = True
    ) -> int:
        """Attach an already-analyzed file to a build.

        Split out from :meth:`attach` because the submission flow has to analyze *before* the
        build exists — the measured dimensions are prefilled into the form the user is about to
        fill in — and can only record once `submit` has assigned an id.
        """
        return await self._store.record_analysis(
            build_id,
            ingested.sha256,
            ingested.analysis,
            primary=primary,
            original_filename=request.filename,
            uploaded_by_discord_id=request.uploaded_by_discord_id,
        )

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        return await self._store.list_for_build(build_id)

    async def content(self, sha256: str) -> bytes:
        """Return stored schematic bytes by digest."""
        content = await self._store.get_file(sha256)
        if content is None:
            raise SchematicNotFoundError
        return content

    async def maintain_storage(self, *, limit: int = 20) -> tuple[int, int]:
        """Backfill and recover artifact state from the database worker."""
        return await self._store.maintain_storage(limit=limit)

    async def primary_for_build(self, build_id: int) -> StoredSchematic | None:
        return await self._store.get_primary(build_id)

    async def find_duplicates(
        self,
        ingested: IngestedSchematic,
        *,
        exclude_build_id: int | None = None,
    ) -> list[DuplicateCandidate]:
        """Find earlier submissions that are identical to or resemble an upload.

        Cheap indexed facts decide only the two trustworthy tiers: content SHA-256 proves
        byte identity, and a version-scoped shape fingerprint proves the same build under
        translation or rotation. Structural hashes and metric ranges only make a shortlist;
        every fuzzy verdict comes from loading both files and comparing them in the worker.
        """
        self._require_available()
        analysis = ingested.analysis
        found: dict[int, DuplicateCandidate] = {}

        file_matches = await self._store.find_file_matches(
            ingested.sha256,
            exclude_build_id=exclude_build_id,
            limit=25,
        )
        for stored in file_matches:
            _retain_better(found, _duplicate_candidate(stored, tier="identical", distance=0.0))

        shape_matches = await self._store.find_fingerprint_matches(
            analysis.fingerprints.shape,
            preset=FingerprintPreset.SHAPE,
            analyzer_version=analysis.analyzer_version,
            exclude_build_id=exclude_build_id,
            limit=25,
        )
        for stored in shape_matches:
            if stored.file_sha256 != ingested.sha256:
                _retain_better(found, _duplicate_candidate(stored, tier="structural-match", distance=0.0))

        if self._duplicate_max_comparisons == 0:
            return _rank_duplicates(found.values(), self._duplicate_result_limit)

        structural_matches = await self._store.find_fingerprint_matches(
            analysis.fingerprints.structural,
            preset=FingerprintPreset.STRUCTURAL,
            analyzer_version=analysis.analyzer_version,
            exclude_build_id=exclude_build_id,
            limit=25,
        )
        metric_matches = await self._store.find_metric_neighbours(
            analysis.metrics,
            tolerance=self._duplicate_metric_tolerance,
            exclude_build_id=exclude_build_id,
            limit=25,
        )
        fuzzy_by_schematic = {
            stored.id: stored
            for stored in (*structural_matches, *metric_matches)
            if stored.build_id not in found and stored.file_sha256 != ingested.sha256
        }
        fuzzy = sorted(fuzzy_by_schematic.values(), key=lambda stored: _metric_distance(analysis, stored))

        left = await self._store.get_file(ingested.sha256)
        if left is None:
            return _rank_duplicates(found.values(), self._duplicate_result_limit)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._duplicate_total_timeout_seconds
        for stored in fuzzy[: self._duplicate_max_comparisons]:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            right = await self._store.get_file(stored.file_sha256)
            if right is None:
                continue
            try:
                comparison = await self._analyzer.compare(
                    left,
                    right,
                    preset=FingerprintPreset.SHAPE,
                    timeout_seconds=remaining,
                )
            except SquidError:
                logger.warning(
                    "Could not compare schematic %s with candidate %s; returning partial duplicate results.",
                    ingested.sha256,
                    stored.file_sha256,
                    exc_info=True,
                    extra={
                        "squid.build.id": stored.build_id,
                        "squid.schematic.format": ingested.analysis.metrics.source_format.value,
                        "squid.schematic.operation": "compare",
                    },
                )
                continue

            if comparison.identical:
                tier: DuplicateTier = "structural-match"
            elif comparison.footprint_distance <= self._duplicate_near_distance:
                tier = "near"
            else:
                continue
            _retain_better(
                found,
                _duplicate_candidate(
                    stored,
                    tier=tier,
                    distance=comparison.footprint_distance,
                    detail=comparison.summary,
                ),
            )

        return _rank_duplicates(found.values(), self._duplicate_result_limit)

    async def prepare_render(self, build_id: int) -> PreparedRender | None:
        """Render a primary schematic once, or return its recipe-matched cached URL.

        Rendering is enrichment: disabled support, an oversized build, a bad pack, or a native
        failure all return `None` so submission and voting continue unaffected.
        """
        if not self._render_enabled or self._resource_pack is None or not self._available:
            self._warn_render_once("Schematic rendering is disabled or unavailable.")
            return None
        try:
            capabilities = await self._analyzer.capabilities()
            if not capabilities.can_render:
                self._warn_render_once("The schematic engine has no rendering adapter.")
                return None
            pack_data, pack_sha256 = await self._resource_pack.load()
        except SquidError:
            self._warn_render_once("The configured schematic resource pack is unavailable.", exc_info=True)
            return None

        stored = await self._store.get_primary(build_id)
        if stored is None:
            return None
        if stored.file_sha256 in self._poisoned:
            return None
        metrics = stored.analysis.metrics
        if metrics.block_count > self._render_max_block_count:
            logger.info(
                "Skipping render for schematic %s: block count %s exceeds cap %s.",
                stored.id,
                metrics.block_count,
                self._render_max_block_count,
                extra={
                    "squid.build.id": stored.build_id,
                    "squid.schematic.format": stored.analysis.metrics.source_format.value,
                    "squid.schematic.operation": "render",
                },
            )
            return None
        if metrics.bounding_volume > self._render_max_bounding_volume:
            logger.info(
                "Skipping render for schematic %s: bounding volume %s exceeds cap %s.",
                stored.id,
                metrics.bounding_volume,
                self._render_max_bounding_volume,
                extra={
                    "squid.build.id": stored.build_id,
                    "squid.schematic.format": stored.analysis.metrics.source_format.value,
                    "squid.schematic.operation": "render",
                },
            )
            return None

        recipe_hash = _render_recipe_hash(stored, self._render_request, pack_sha256)
        cached = await self._store.get_render(stored.id, recipe_hash)
        if cached is not None:
            return PreparedRender(
                schematic_id=stored.id,
                recipe_hash=recipe_hash,
                width=cached.width,
                height=cached.height,
                cached_url=cached.url,
            )

        attempt = (stored.id, recipe_hash)
        if attempt in self._render_attempted:
            return None
        self._render_attempted.add(attempt)
        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            return None
        try:
            png = await self._analyzer.render(data, request=self._render_request, resource_pack=pack_data)
        except SchematicWorkerCrashedError:
            self._poisoned.add(stored.file_sha256)
            logger.warning(
                "The schematic worker crashed while rendering build %s; skipping preview.",
                build_id,
                extra={
                    "squid.build.id": build_id,
                    "squid.schematic.format": stored.analysis.metrics.source_format.value,
                    "squid.schematic.operation": "render",
                },
            )
            return None
        except SquidError:
            logger.warning(
                "Could not render the schematic for build %s; skipping preview.",
                build_id,
                exc_info=True,
                extra={
                    "squid.build.id": build_id,
                    "squid.schematic.format": stored.analysis.metrics.source_format.value,
                    "squid.schematic.operation": "render",
                },
            )
            return None
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            logger.warning(
                "The schematic renderer returned a non-PNG payload for build %s.",
                build_id,
                extra={
                    "squid.build.id": build_id,
                    "squid.schematic.format": stored.analysis.metrics.source_format.value,
                    "squid.schematic.operation": "render",
                },
            )
            return None
        return PreparedRender(
            schematic_id=stored.id,
            recipe_hash=recipe_hash,
            width=self._render_request.width,
            height=self._render_request.height,
            png=png,
        )

    async def record_render(self, render: PreparedRender, url: str) -> StoredRender:
        """Persist the uploaded URL for a freshly prepared render."""
        if render.png is None:
            msg = "Only a fresh render can be recorded."
            raise ValueError(msg)
        return await self._store.record_render(
            render.schematic_id,
            render.recipe_hash,
            url,
            width=render.width,
            height=render.height,
            byte_size=len(render.png),
        )

    def _warn_render_once(self, message: str, *, exc_info: bool = False) -> None:
        if self._render_warning_emitted:
            return
        self._render_warning_emitted = True
        logger.warning(message, exc_info=exc_info)

    async def convert(
        self, build_id: int, request: ConvertRequest, *, version_label: str | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        """Re-encode a build's primary schematic, optionally retargeting its data version."""
        self._require_available()
        stored = await self._store.get_primary(build_id)
        if stored is None:
            raise SchematicNotFoundError(context={"build_id": build_id}, public_context={"build_id": build_id})

        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            msg = "The stored schematic file is missing."
            raise SchematicNotFoundError(msg, context={"sha256": stored.file_sha256})

        data_version = request.target_data_version
        if data_version is None and version_label is not None:
            data_version = await self._versions.data_version_for(version_label)
            if data_version is None:
                msg = "That Minecraft version has no known data version."
                raise InvalidSchematicError(
                    msg,
                    context={"version": version_label},
                    public_context={"version": version_label},
                    end_user_action="Pick a Java version the bot knows, or give a data version directly.",
                )

        return await self._analyzer.convert(data, target=request.target_format, data_version=data_version)

    async def detect_lattice(self, build_id: int) -> AutostackLattice | None:
        """Return the opportunistically detected repeating unit for a build."""
        self._require_available()
        stored = await self._store.get_primary(build_id)
        if stored is None:
            raise SchematicNotFoundError(context={"build_id": build_id}, public_context={"build_id": build_id})
        return stored.analysis.lattice

    async def measure_timing(
        self,
        build_id: int,
        *,
        input_position: Vector3 | None = None,
        max_ticks: int = 200,
    ) -> SimulationResult:
        """Run and persist staff-facing timing evidence without editing the build timing."""
        self._require_available()
        capabilities = await self._analyzer.capabilities()
        if not capabilities.can_simulate:
            msg = "This schematic engine does not provide the verified tick simulator."
            raise SchematicSupportUnavailableError(msg)
        stored = await self._store.get_primary(build_id)
        if stored is None:
            raise SchematicNotFoundError(context={"build_id": build_id}, public_context={"build_id": build_id})
        if stored.file_sha256 in self._poisoned:
            msg = "This file has already crashed the schematic engine on this instance."
            raise InvalidSchematicError(msg)
        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            raise SchematicNotFoundError(context={"sha256": stored.file_sha256})
        try:
            result = await self._analyzer.simulate(
                data,
                request=SimulationRequest(input_position=input_position, max_ticks=max_ticks),
            )
        except SchematicWorkerCrashedError:
            self._poisoned.add(stored.file_sha256)
            raise
        await self._store.record_simulation(stored.id, result)
        return result

    async def aclose(self) -> None:
        """Release the analyzer's process-level resources at shutdown."""
        await self._analyzer.aclose()

    async def bytes_for(self, sha256: str) -> bytes:
        """Return stored schematic bytes, or raise if they are gone."""
        data = await self._store.get_file(sha256)
        if data is None:
            raise SchematicNotFoundError(context={"sha256": sha256}, public_context={"sha256": sha256})
        return data

    def _vet(self, data: bytes, *, filename: str) -> SchematicFormat:
        """Prove the bytes are a bounded, recognisable schematic before the engine sees them.

        Each step is cheap and total, and each one runs only on input the previous step already
        accepted: size, then inflation budget, then content typing.
        """
        if len(data) > self._limits.max_upload_bytes:
            raise SchematicTooLargeError(actual=len(data), limit=self._limits.max_upload_bytes, measure="file size")

        # Raises DecompressionBudgetExceededError long before the bomb costs us anything.
        inflated_size_at_most(data, self._limits.max_inflated_bytes)

        source_format = sniff_schematic_format(
            data, filename_hint=filename, max_sniff_bytes=self._limits.max_sniff_bytes
        )
        if source_format is None:
            raise InvalidSchematicError(context={"filename": filename})
        return source_format

    def _require_available(self) -> None:
        if not self._available:
            raise SchematicSupportUnavailableError


def summarise_losses(losses: Sequence[VersionLossEntry], *, limit: int = 10) -> str:
    """Render a fidelity-loss report as human-readable lines."""
    if not losses:
        return "No fidelity loss reported."
    lines = [f"- **{loss.severity}** `{loss.path or loss.kind}` — {loss.detail}" for loss in losses[:limit]]
    if len(losses) > limit:
        lines.append(f"- …and {len(losses) - limit} more.")
    return "\n".join(lines)


def _render_recipe_hash(stored: StoredSchematic, request: RenderRequest, pack_sha256: str) -> str:
    dimensions = stored.analysis.metrics.dimensions
    recipe = {
        "pack_sha256": pack_sha256,
        "request": request.recipe_fields(),
        "schematic_dimensions": [dimensions.width, dimensions.height, dimensions.length],
        "analyzer_version": stored.analysis.analyzer_version,
    }
    encoded = json.dumps(recipe, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_DUPLICATE_TIER_ORDER: dict[DuplicateTier, int] = {"identical": 0, "structural-match": 1, "near": 2}


def _duplicate_candidate(
    stored: StoredSchematic,
    *,
    tier: DuplicateTier,
    distance: float,
    detail: str | None = None,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        build_id=stored.build_id,
        schematic_id=stored.id,
        tier=tier,
        footprint_distance=distance,
        detail=detail,
    )


def _retain_better(found: dict[int, DuplicateCandidate], candidate: DuplicateCandidate) -> None:
    existing = found.get(candidate.build_id)
    if existing is None or (_DUPLICATE_TIER_ORDER[candidate.tier], candidate.footprint_distance) < (
        _DUPLICATE_TIER_ORDER[existing.tier],
        existing.footprint_distance,
    ):
        found[candidate.build_id] = candidate


def _rank_duplicates(candidates: Iterable[DuplicateCandidate], limit: int) -> list[DuplicateCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            _DUPLICATE_TIER_ORDER[candidate.tier],
            candidate.footprint_distance,
            candidate.build_id,
        ),
    )[:limit]


def _metric_distance(current: SchematicAnalysis, stored: StoredSchematic) -> float:
    """Give the most similar metric candidate the scarce worker slots first."""
    left = current.metrics
    right = stored.analysis.metrics
    block_scale = max(left.block_count, right.block_count, 1)
    score = abs(left.block_count - right.block_count) / block_scale
    left_dimensions = sorted((left.dimensions.width, left.dimensions.height, left.dimensions.length))
    right_dimensions = sorted((right.dimensions.width, right.dimensions.height, right.dimensions.length))
    for left_extent, right_extent in zip(left_dimensions, right_dimensions, strict=True):
        score += abs(left_extent - right_extent) / max(left_extent, right_extent, 1)
    return score
