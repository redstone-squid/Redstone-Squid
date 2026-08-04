"""SQLAlchemy persistence adapter for stored schematics."""

import hashlib
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicAnalysis,
    SchematicFormat,
    SchematicMetrics,
)
from squid.schematics.infrastructure.mapping import to_row_values, to_stored_schematic
from squid.schematics.infrastructure.models import BuildSchematic, SchematicFile

_FINGERPRINT_COLUMNS = {
    FingerprintPreset.STRUCTURAL: BuildSchematic.fingerprint_structural,
    FingerprintPreset.SHAPE: BuildSchematic.fingerprint_shape,
    FingerprintPreset.EXACT: BuildSchematic.fingerprint_exact,
}


class SchematicRepository:
    """Persist schematic bytes and their analyses."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        """Store bytes content-addressed by SHA-256, returning the digest.

        Idempotent by construction: re-uploading the same file is a no-op insert rather than a
        conflict, which is what makes "byte-identical to an existing submission" cheap to
        detect before any analysis runs.
        """
        digest = hashlib.sha256(data).hexdigest()
        statement = (
            insert(SchematicFile)
            .values(
                sha256=digest,
                data=data,
                byte_size=len(data),
                source_format=source_format.value,
            )
            .on_conflict_do_nothing(index_elements=[SchematicFile.sha256])
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()
        return digest

    async def get_file(self, sha256: str) -> bytes | None:
        async with self._session_factory() as session:
            return await session.scalar(select(SchematicFile.data).where(SchematicFile.sha256 == sha256))

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
        """Attach an analysis to a build, replacing any earlier analysis of the same file.

        Re-analysing after an engine upgrade must overwrite rather than accumulate, so the
        `(build_id, file_sha256)` uniqueness doubles as the upsert key.
        """
        values = {
            "build_id": build_id,
            "file_sha256": sha256,
            "is_primary": primary,
            "original_filename": original_filename,
            "uploaded_by_discord_id": uploaded_by_discord_id,
            **to_row_values(analysis),
        }
        updatable = {key: value for key, value in values.items() if key not in ("build_id", "file_sha256")}
        statement = (
            insert(BuildSchematic)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[BuildSchematic.build_id, BuildSchematic.file_sha256],
                set_=updatable,
            )
            .returning(BuildSchematic.id)
        )
        async with self._session_factory() as session:
            if primary:
                # The partial unique index allows only one primary per build, so the previous
                # holder must be demoted in the same transaction as the new one is promoted.
                await session.execute(
                    update(BuildSchematic)
                    .where(and_(BuildSchematic.build_id == build_id, BuildSchematic.file_sha256 != sha256))
                    .values(is_primary=False)
                )
            schematic_id = await session.scalar(statement)
            await session.commit()
        assert schematic_id is not None
        return schematic_id

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        return await self._fetch(self._joined().where(BuildSchematic.build_id == build_id))

    async def get_primary(self, build_id: int) -> StoredSchematic | None:
        found = await self._fetch(
            self._joined().where(and_(BuildSchematic.build_id == build_id, BuildSchematic.is_primary)).limit(1)
        )
        return found[0] if found else None

    async def find_file_matches(
        self,
        sha256: str,
        *,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        """Find attachments pointing at the same content-addressed file."""
        statement = self._joined().where(BuildSchematic.file_sha256 == sha256)
        if exclude_build_id is not None:
            statement = statement.where(BuildSchematic.build_id != exclude_build_id)
        return await self._fetch(statement.limit(limit))

    async def find_fingerprint_matches(
        self,
        fingerprint: str,
        *,
        preset: FingerprintPreset,
        analyzer_version: str,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        """Return schematics sharing a fingerprint under one analyzer version.

        The `analyzer_version` filter is not optional: fingerprints are hashes whose definition
        can change between engine releases, so comparing across versions would silently return
        garbage rather than nothing.
        """
        column = _FINGERPRINT_COLUMNS[preset]
        statement = self._joined().where(
            and_(column == fingerprint, BuildSchematic.analyzer_version == analyzer_version)
        )
        if exclude_build_id is not None:
            statement = statement.where(BuildSchematic.build_id != exclude_build_id)
        return await self._fetch(statement.limit(limit))

    async def find_metric_neighbours(
        self,
        metrics: SchematicMetrics,
        *,
        tolerance: float,
        limit: int = 25,
        exclude_build_id: int | None = None,
    ) -> list[StoredSchematic]:
        """Shortlist schematics of comparable size for pairwise near-duplicate ranking.

        Deliberately coarse. This exists to hand the worker a handful of candidates, not to
        decide anything: block count and sorted dimensions survive translation and rotation,
        which is exactly the kind of resubmission an exact hash misses.
        """
        blocks = metrics.block_count
        low, high = int(blocks * (1 - tolerance)), int(blocks * (1 + tolerance)) + 1
        sorted_dimensions = sorted((metrics.dimensions.width, metrics.dimensions.height, metrics.dimensions.length))
        span = max(1, int(max(sorted_dimensions) * tolerance))
        statement = self._joined().where(
            and_(
                BuildSchematic.block_count.between(low, high),
                # Orientation-blind: compare the sorted extents rather than the named axes.
                or_(*(_dimension_within(value, span) for value in sorted_dimensions)),
            )
        )
        if exclude_build_id is not None:
            statement = statement.where(BuildSchematic.build_id != exclude_build_id)
        return await self._fetch(statement.order_by(func.abs(BuildSchematic.block_count - blocks)).limit(limit))

    async def record_render(self, schematic_id: int, recipe_hash: str, url: str) -> None:
        """Record a rendered preview. Phase 3 introduces the table this will write to."""
        msg = "Rendered previews are a Phase 3 feature; schematic_renders does not exist yet."
        raise NotImplementedError(msg)

    @staticmethod
    def _joined() -> Select[tuple[BuildSchematic, str, int]]:
        """Select an attachment together with the two facts that live on its file row."""
        return select(BuildSchematic, SchematicFile.source_format, SchematicFile.byte_size).join(
            SchematicFile, BuildSchematic.file_sha256 == SchematicFile.sha256
        )

    async def _fetch(self, statement: Select[tuple[BuildSchematic, str, int]]) -> list[StoredSchematic]:
        async with self._session_factory() as session:
            rows: Sequence[tuple[BuildSchematic, str, int]] = (await session.execute(statement)).all()  # type: ignore[assignment]
        return [
            to_stored_schematic(row, source_format=SchematicFormat(source_format), byte_size=byte_size)
            for row, source_format, byte_size in rows
        ]


def _dimension_within(value: int, span: int) -> ColumnElement[bool]:
    """Match rows any of whose extents is within `span` of `value`."""
    return or_(
        BuildSchematic.width.between(value - span, value + span),
        BuildSchematic.height.between(value - span, value + span),
        BuildSchematic.length.between(value - span, value + span),
    )
