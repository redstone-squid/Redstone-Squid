"""Schematic application services."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from squid.schematics.application.commands import ConvertRequest, IngestRequest
from squid.schematics.application.ports import SchematicAnalyzer, SchematicStore, SchematicVersionResolver
from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain.formats import inflated_size_at_most, sniff_schematic_format
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    SchematicAnalysis,
    SchematicFormat,
    SchematicLimits,
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
    ) -> None:
        self._analyzer = analyzer
        self._store = store
        self._versions = versions
        self._limits = limits or SchematicLimits()
        self._available = engine_installed
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

    async def primary_for_build(self, build_id: int) -> StoredSchematic | None:
        return await self._store.get_primary(build_id)

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
