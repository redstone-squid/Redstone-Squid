"""SQLAlchemy persistence adapter for stored schematics."""

from collections.abc import Callable

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.models import Build
from squid.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicAnalysis,
    SchematicMetrics,
    SimulationResult,
)
from squid.schematics.infrastructure.mapping import simulation_to_json, to_row_values, to_stored_schematic
from squid.schematics.infrastructure.preview_publisher import PostgresSchematicPreviewRepository
from squid.schematics.models import BuildSchematic, SchematicFile

_FINGERPRINT_COLUMNS = {
    FingerprintPreset.STRUCTURAL: BuildSchematic.fingerprint_structural,
    FingerprintPreset.SHAPE: BuildSchematic.fingerprint_shape,
    FingerprintPreset.EXACT: BuildSchematic.fingerprint_exact,
}


class PostgresSchematicRepository:
    """Persist schematic metadata and attachments within the caller-owned transaction."""

    def __init__(
        self,
        session: Callable[[], AsyncSession],
    ) -> None:
        self._session = session
        self._previews = PostgresSchematicPreviewRepository(session)

    async def register_file(self, file: SchematicFile) -> None:
        await self._session().execute(
            insert(SchematicFile)
            .values(
                sha256=file.sha256,
                object_key=file.object_key,
                byte_size=file.byte_size,
                source_format=file.source_format,
            )
            .on_conflict_do_nothing(index_elements=[SchematicFile.sha256])
        )

    async def get_file_metadata(self, sha256: str) -> SchematicFile | None:
        return await self._session().get(SchematicFile, sha256)

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
        """Insert or replace the analysis for this build and file, returning its attachment ID.

        Lock the build before changing its featured attachment. Selecting a primary attachment clears
        the previous generated preview and queues a replacement. publication=None preserves existing
        publication metadata when updating an attachment.
        """
        values = {
            "build_id": build_id,
            "file_sha256": sha256,
            "is_primary": primary,
            "original_filename": original_filename,
            "uploaded_by_account_id": uploaded_by_account_id,
            **to_row_values(analysis),
        }
        if publication is not None:
            values.update(
                visibility=publication.visibility.value,
                license_code=publication.license.value if publication.license is not None else None,
                rights_attested_at=publication.rights_attested_at,
                rights_attested_by_account_id=publication.rights_attested_by_account_id,
                published_at=publication.published_at,
                withdrawn_at=publication.withdrawn_at,
            )
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
        session = self._session()
        if primary:
            await session.scalar(select(Build.id).where(Build.id == build_id).with_for_update())
            # The partial unique index allows only one primary per build, so the previous
            # holder must be demoted in the same transaction as the new one is promoted.
            await session.execute(
                update(BuildSchematic)
                .where(and_(BuildSchematic.build_id == build_id, BuildSchematic.file_sha256 != sha256))
                .values(is_primary=False)
            )
        schematic_id = await session.scalar(statement)
        if primary:
            await self._previews.featured_attachment_changed(build_id)
        await session.flush()
        assert schematic_id is not None
        return schematic_id

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        return await self._fetch(self._joined().where(BuildSchematic.build_id == build_id))

    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None:
        found = await self._fetch(
            self._joined().where(
                BuildSchematic.build_id == build_id,
                BuildSchematic.id == schematic_id,
            )
        )
        return found[0] if found else None

    async def get_featured(self, build_id: int) -> StoredSchematic | None:
        """Select the primary attachment as featured, with no fallback to secondary attachments."""
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
        """Return matches produced by the same analyzer version.

        Fingerprint definitions can change between engine releases, so comparisons exclude other versions.
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
        """Shortlist candidates by block count and approximate size, closest block count first.

        At least one stored axis must be near one requested extent; this does not compare all three
        dimensions as a set. Pairwise analysis makes the eventual similarity decision.
        """
        blocks = metrics.block_count
        low, high = int(blocks * (1 - tolerance)), int(blocks * (1 + tolerance)) + 1
        sorted_dimensions = sorted((metrics.dimensions.width, metrics.dimensions.height, metrics.dimensions.length))
        span = max(1, int(max(sorted_dimensions) * tolerance))
        statement = self._joined().where(
            and_(
                BuildSchematic.block_count.between(low, high),
                # Match any axis against any requested extent to keep rotated candidates eligible.
                or_(*(_dimension_within(value, span) for value in sorted_dimensions)),
            )
        )
        if exclude_build_id is not None:
            statement = statement.where(BuildSchematic.build_id != exclude_build_id)
        return await self._fetch(statement.order_by(func.abs(BuildSchematic.block_count - blocks)).limit(limit))

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        """Replace the latest moderator-triggered evidence for one schematic."""
        session = self._session()
        await session.execute(
            update(BuildSchematic)
            .where(BuildSchematic.id == schematic_id)
            .values(simulation_evidence=simulation_to_json(result))
        )
        await session.flush()

    @staticmethod
    def _joined() -> Select[BuildSchematic, str, int]:
        """Select an attachment together with the two facts that live on its file row."""
        return select(BuildSchematic, SchematicFile.source_format, SchematicFile.byte_size).join(
            SchematicFile, BuildSchematic.file_sha256 == SchematicFile.sha256
        )

    async def _fetch(self, statement: Select[BuildSchematic, str, int]) -> list[StoredSchematic]:
        session = self._session()
        rows = (await session.execute(statement)).all()
        return [
            to_stored_schematic(row, source_format=source_format, byte_size=byte_size)
            for row, source_format, byte_size in rows
        ]


def _dimension_within(value: int, span: int) -> ColumnElement[bool]:
    """Match rows any of whose extents is within `span` of `value`."""
    return or_(
        BuildSchematic.width.between(value - span, value + span),
        BuildSchematic.height.between(value - span, value + span),
        BuildSchematic.length.between(value - span, value + span),
    )
