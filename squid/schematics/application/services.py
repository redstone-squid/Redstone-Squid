"""Schematic application services."""

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from squid.core.errors import DataIntegrityError, SquidError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, offset_page
from squid.schematics.application.commands import ConvertRequest, IngestRequest, RenderRequest, SimulationRequest
from squid.schematics.application.ports import (
    SchematicAnalyzer,
    SchematicResourcePackProvider,
    SchematicStore,
    SchematicVersionResolver,
)
from squid.schematics.application.queries import (
    CachedRender,
    DuplicateCandidate,
    DuplicateTier,
    FreshRender,
    PublicSchematicDownload,
    RenderedSchematic,
    RenderPreparation,
    RenderSkipReason,
    SchematicPublication,
    SkippedRender,
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
    SchematicRenderRefusedError,
    SchematicRenderUnavailableError,
    SchematicSupportUnavailableError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)

logger = logging.getLogger(__name__)

_MAX_RENDER_CONTENT_BYTES = 8 * 1024 * 1024
"""The largest stored preview worth loading into a response or a Discord upload."""


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

        self._refuse_poisoned(sha256)

        with self._quarantining(sha256, operation="analyze"):
            analysis = await self._analyzer.analyze(
                request.data,
                limits=self._limits,
                with_lattice=request.with_lattice,
                source_format=source_format,
            )
        return IngestedSchematic(sha256, analysis)

    async def attach(
        self,
        build_id: int,
        request: IngestRequest,
        *,
        primary: bool = True,
        publication: SchematicPublication | None = None,
    ) -> IngestedSchematic:
        """Ingest a file and record it against a build that already exists."""
        ingested = await self.ingest(request)
        await self.record(build_id, ingested, request, primary=primary, publication=publication)
        return ingested

    async def record(
        self,
        build_id: int,
        ingested: IngestedSchematic,
        request: IngestRequest,
        *,
        primary: bool = True,
        publication: SchematicPublication | None = None,
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
            uploaded_by_account_id=request.uploaded_by_account_id,
            publication=publication,
        )

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        return await self._store.list_for_build(build_id)

    async def list_public_for_build(self, build_id: int) -> list[StoredSchematic]:
        """Return only explicitly published, sanitized, non-withdrawn attachments."""
        return [
            schematic
            for schematic in await self._store.list_for_build(build_id)
            if schematic.publication.is_public_downloadable
        ]

    async def list_public_page(
        self,
        build_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 50,
    ) -> Page[StoredSchematic]:
        """Return one page of a build's publicly downloadable attachments.

        Paging belongs here rather than in the route, which was the last list
        endpoint slicing its own results after builds, records, and notifications
        moved theirs into their services.

        Slicing in memory is deliberate for now: a single build's attachment
        count is small and bounded in practice, and the store port has no
        offset/limit. The trigger to change that is one build's attachments no
        longer fitting comfortably in one query -- then `list_for_build` gains
        `offset`/`limit` and a count, and this body changes without the route
        noticing.
        """
        return offset_page(
            await self.list_public_for_build(build_id),
            offset=selector.offset,
            page_size=page_size,
        )

    async def public_download(self, build_id: int, schematic_id: int) -> PublicSchematicDownload:
        """Return canonical bytes only through an attachment's explicit publication policy."""
        stored = await self._store.get_for_build(build_id, schematic_id)
        if stored is None or not stored.publication.is_public_downloadable:
            raise SchematicNotFoundError
        content = await self._store.get_file(stored.file_sha256)
        if content is None:
            raise SchematicNotFoundError
        license_ = stored.publication.license
        if license_ is None:
            # `SchematicPublication` rejects a public attachment without a license, so a
            # row reaching here means persistence bypassed the domain constructor.
            msg = "A publicly downloadable schematic carries no license."
            raise DataIntegrityError(msg, context={"build_id": build_id, "schematic_id": schematic_id})
        return PublicSchematicDownload(
            content=content,
            schematic=stored,
            license=license_,
            source_format=stored.analysis.metrics.source_format,
        )

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

    async def prepare_render(self, build_id: int) -> RenderPreparation:
        """Decide what a build's preview should be: a fresh PNG, a cached URL, or a skip.

        Every permanent outcome is named by a `RenderSkipReason` so the durable render queue
        can acknowledge it and a moderator surface can explain it. Operational failures — no
        resource pack, a dead worker, a renderer that answered with something other than a
        PNG — propagate instead, so the queue retries or dead-letters the intent.
        """
        if not self._render_enabled:
            return SkippedRender(RenderSkipReason.RENDERING_DISABLED)
        pack_data, pack_sha256 = await self._render_resources()

        stored = await self._store.get_primary(build_id)
        if stored is None:
            return SkippedRender(RenderSkipReason.NO_PRIMARY_SCHEMATIC)
        reason = self._render_skip_reason(stored)
        if reason is not None:
            self._log_render_skip(stored, reason)
            return SkippedRender(reason)

        recipe_hash = _render_recipe_hash(stored, self._render_request, pack_sha256)
        cached = await self._store.get_render(stored.id, recipe_hash)
        if cached is not None:
            return CachedRender(
                schematic_id=stored.id,
                recipe_hash=recipe_hash,
                width=cached.width,
                height=cached.height,
                url=cached.url,
            )

        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            self._log_render_skip(stored, RenderSkipReason.MISSING_FILE)
            return SkippedRender(RenderSkipReason.MISSING_FILE)
        png = await self._render_png(stored, data, pack_data, self._render_request)
        return FreshRender(
            schematic_id=stored.id,
            recipe_hash=recipe_hash,
            width=self._render_request.width,
            height=self._render_request.height,
            png=png,
        )

    def render_recipe(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
        zoom: float | None = None,
    ) -> RenderRequest:
        """Return this instance's configured framing with the caller's overrides applied.

        Transports build their request through here rather than constructing one, so a caller
        who asks for nothing in particular reproduces the recipe the durable queue renders
        with — and therefore hits its cached preview instead of paying for an identical image
        under a different hash.
        """
        base = self._render_request
        return dataclasses.replace(
            base,
            width=base.width if width is None else width,
            height=base.height if height is None else height,
            yaw=base.yaw if yaw is None else yaw,
            pitch=base.pitch if pitch is None else pitch,
            zoom=base.zoom if zoom is None else zoom,
        )

    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        """Render a build's primary schematic for a caller who is waiting for the image.

        Distinct from `prepare_render`, which serves the durable projection queue: that path
        decides what a build's *published* preview should be and hands its transport a PNG to
        upload, while this one answers one request and publishes nothing. A recipe the queue
        has already rendered is served from its stored artifact, so the default framing costs
        an object-storage read rather than a render.

        Deliberately not written back to the cache. Recording a render also projects it onto
        the build as its preview, and a request whose only claim to authority is that somebody
        asked for it must not decide what a build looks like to everyone else.
        """
        self._require_available()
        if not self._render_enabled:
            raise SchematicRenderUnavailableError

        stored = await self._store.get_primary(build_id)
        if stored is None:
            raise SchematicNotFoundError(context={"build_id": build_id}, public_context={"build_id": build_id})
        # Judged before the resource pack is acquired: a build that can never be previewed
        # should not make this process fetch a pack to find that out.
        reason = self._render_skip_reason(stored)
        if reason is not None:
            raise _refused(reason)

        render_request = request or self._render_request
        pack_data, pack_sha256 = await self._render_resources()
        recipe_hash = _render_recipe_hash(stored, render_request, pack_sha256)
        if await self._store.get_render(stored.id, recipe_hash) is not None:
            cached = await self._store.get_render_content(recipe_hash, max_bytes=_MAX_RENDER_CONTENT_BYTES)
            if cached is not None:
                return RenderedSchematic(
                    build_id=stored.build_id,
                    schematic_id=stored.id,
                    recipe_hash=recipe_hash,
                    width=render_request.width,
                    height=render_request.height,
                    png=cached,
                    from_cache=True,
                )

        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            raise SchematicNotFoundError(context={"sha256": stored.file_sha256})
        png = await self._render_png(stored, data, pack_data, render_request)
        return RenderedSchematic(
            build_id=stored.build_id,
            schematic_id=stored.id,
            recipe_hash=recipe_hash,
            width=render_request.width,
            height=render_request.height,
            png=png,
            from_cache=False,
        )

    def explain_render_skip(self, stored: StoredSchematic) -> RenderSkipReason | None:
        """Say why this attachment will never be previewed, or `None` if it is eligible.

        Pure and free on purpose. A moderator asking what the bot knows about a build must not
        set a GPU render going, so this answers from configuration and the stored analysis
        alone — it cannot report the cache, the resource pack, or the renderer's own health.
        """
        if not self._render_enabled:
            return RenderSkipReason.RENDERING_DISABLED
        return self._render_skip_reason(stored)

    async def _render_resources(self) -> tuple[bytes, str]:
        """Acquire the capability and resource pack a render needs, or refuse operationally."""
        if self._resource_pack is None or not self._available:
            msg = "Schematic rendering is enabled but its worker resources are unavailable."
            raise SchematicRenderUnavailableError(msg)
        try:
            capabilities = await self._analyzer.capabilities()
            if not capabilities.can_render:
                msg = capabilities.unavailable_reason or "The schematic engine has no rendering adapter."
                raise SchematicRenderUnavailableError(msg)
            return await self._resource_pack.load()
        except SquidError:
            self._warn_render_once("The configured schematic resource pack is unavailable.", exc_info=True)
            raise

    def _render_skip_reason(self, stored: StoredSchematic) -> RenderSkipReason | None:
        """Judge one attachment's eligibility, with no I/O and no engine involved."""
        if not stored.publication.is_sanitized:
            return RenderSkipReason.NOT_SANITIZED
        if stored.file_sha256 in self._poisoned:
            return RenderSkipReason.POISONED_FILE
        metrics = stored.analysis.metrics
        if metrics.block_count > self._render_max_block_count:
            return RenderSkipReason.OVER_BLOCK_BUDGET
        if metrics.bounding_volume > self._render_max_bounding_volume:
            return RenderSkipReason.OVER_VOLUME_BUDGET
        return None

    async def _render_png(
        self, stored: StoredSchematic, data: bytes, pack_data: bytes, request: RenderRequest
    ) -> bytes:
        """Run one native render and prove its output is really a PNG."""
        with self._quarantining(stored.file_sha256, operation="render", stored=stored):
            try:
                png = await self._analyzer.render(data, request=request, resource_pack=pack_data)
            except SchematicWorkerCrashedError:
                # Already logged and quarantined by the context manager; re-raised here only so
                # a crash does not also fall into the generic operational branch below.
                raise
            except SquidError:
                logger.warning(
                    "Could not render the schematic for build %s; the durable queue will retry.",
                    stored.build_id,
                    exc_info=True,
                    extra=_log_fields(stored, "render"),
                )
                raise
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            logger.warning(
                "The schematic renderer returned a non-PNG payload for build %s.",
                stored.build_id,
                extra=_log_fields(stored, "render"),
            )
            raise SchematicRenderUnavailableError()
        return png

    def _log_render_skip(self, stored: StoredSchematic, reason: RenderSkipReason) -> None:
        logger.info(
            "Skipping the render for schematic %s: %s.",
            stored.id,
            reason.value,
            extra={**_log_fields(stored, "render"), "squid.schematic.render_skip_reason": reason.value},
        )

    async def record_render(self, render: FreshRender, url: str, object_key: str) -> StoredRender | None:
        """Persist and project a fresh render if its source is still the primary schematic."""
        return await self._store.record_render(
            render.schematic_id,
            render.recipe_hash,
            url,
            object_key,
            width=render.width,
            height=render.height,
            byte_size=len(render.png),
        )

    async def project_render(self, render: CachedRender) -> bool:
        """Project a cached render if its source is still the primary schematic."""
        return await self._store.project_render(render.schematic_id, render.recipe_hash, render.url)

    async def render_content(self, recipe_hash: str, *, max_bytes: int = _MAX_RENDER_CONTENT_BYTES) -> bytes:
        """Return one registered PNG preview from private object storage."""
        content = await self._store.get_render_content(recipe_hash, max_bytes=max_bytes)
        if content is None:
            raise SchematicNotFoundError(context={"recipe_hash": recipe_hash})
        return content

    @contextlib.contextmanager
    def _quarantining(
        self,
        sha256: str,
        *,
        operation: str,
        stored: StoredSchematic | None = None,
    ) -> Iterator[None]:
        """Quarantine a file that kills a worker, so the next request refuses it outright.

        Analysis, rendering, and simulation all have to do this, and all three used to spell
        it out themselves. Only the log line differs per operation, so only that is a
        parameter; whether the caller then skips, retries, or fails is left to the caller,
        because that policy genuinely does differ — a render is durable work the queue will
        retry, an upload is a user waiting on an answer.
        """
        try:
            yield
        except SchematicWorkerCrashedError:
            self._poisoned.add(sha256)
            fields = _log_fields(stored, operation) if stored is not None else {"squid.schematic.operation": operation}
            logger.warning(
                "The schematic worker crashed during %s; the file is quarantined on this instance.",
                operation,
                extra=fields,
            )
            raise

    def _refuse_poisoned(self, sha256: str) -> None:
        """Refuse a file that has already killed a worker here, rather than killing another."""
        if sha256 in self._poisoned:
            msg = "This file has already crashed the schematic engine on this instance."
            raise InvalidSchematicError(
                msg,
                context={"sha256": sha256},
                end_user_action="Re-export the schematic and try again.",
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
        self._refuse_poisoned(stored.file_sha256)
        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            raise SchematicNotFoundError(context={"sha256": stored.file_sha256})
        with self._quarantining(stored.file_sha256, operation="simulate", stored=stored):
            result = await self._analyzer.simulate(
                data,
                request=SimulationRequest(input_position=input_position, max_ticks=max_ticks),
            )
        await self._store.record_simulation(stored.id, result)
        return result

    async def aclose(self) -> None:
        """Release analyzer and resource-pack process resources at shutdown."""
        await self._analyzer.aclose()
        if self._resource_pack is not None:
            await self._resource_pack.aclose()

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


def _refused(reason: RenderSkipReason) -> SchematicRenderRefusedError:
    """Turn a permanent skip into the refusal a waiting caller is owed.

    The queue treats these as "acknowledge and move on"; someone who asked for the image has
    to be told which of them happened, and `description` is already the sentence to tell them.
    """
    return SchematicRenderRefusedError(reason.value, reason.description)


def _log_fields(stored: StoredSchematic, operation: str) -> dict[str, str | int]:
    """The structured fields every schematic-operation log line here carries."""
    return {
        "squid.build.id": stored.build_id,
        "squid.schematic.format": stored.analysis.metrics.source_format.value,
        "squid.schematic.operation": operation,
    }


def _render_recipe_hash(stored: StoredSchematic, request: RenderRequest, pack_sha256: str) -> str:
    dimensions = stored.analysis.metrics.dimensions
    recipe = {
        "file_sha256": stored.file_sha256,
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
