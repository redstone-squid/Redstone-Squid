"""SQLAlchemy persistence adapter for stored schematics."""

import hashlib
from collections.abc import Sequence
from typing import cast

from sqlalchemy import ColumnElement, Select, Table, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.artifacts import ArtifactStore
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.models import BuildLink
from squid.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.schematics.application.previews import StoredRender
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicAnalysis,
    SchematicFormat,
    SchematicMetrics,
    SimulationResult,
)
from squid.schematics.infrastructure.mapping import simulation_to_json, to_row_values, to_stored_schematic
from squid.schematics.infrastructure.models import (
    BuildSchematic,
    SchematicFile,
    SchematicRender,
    SchematicRenderQueueItem,
)

_FINGERPRINT_COLUMNS = {
    FingerprintPreset.STRUCTURAL: BuildSchematic.fingerprint_structural,
    FingerprintPreset.SHAPE: BuildSchematic.fingerprint_shape,
    FingerprintPreset.EXACT: BuildSchematic.fingerprint_exact,
}
_BUILD_TABLE = cast(Table, SQLBuild.__table__)


class SchematicRepository:
    """Persist schematic metadata and store payloads in a bounded artifact adapter."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], artifacts: ArtifactStore) -> None:
        self._session_factory = session_factory
        self._artifacts = artifacts

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        """Store bytes content-addressed by SHA-256, returning the digest.

        Idempotent by construction: re-uploading the same file is a no-op insert rather than a
        conflict, which is what makes "byte-identical to an existing submission" cheap to
        detect before any analysis runs.
        """
        digest = hashlib.sha256(data).hexdigest()
        object_key = _schematic_object_key(digest)
        await self._write_verified_object(digest, object_key, data)
        statement = (
            insert(SchematicFile)
            .values(
                sha256=digest,
                object_key=object_key,
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
            row = (
                await session.execute(
                    select(
                        SchematicFile.object_key,
                        SchematicFile.byte_size,
                    ).where(SchematicFile.sha256 == sha256)
                )
            ).one_or_none()
        if row is None:
            return None
        data = await self._artifacts.get(row.object_key, max_bytes=row.byte_size)
        if data is None or len(data) != row.byte_size or hashlib.sha256(data).hexdigest() != sha256:
            msg = f"Object storage returned corrupt or missing schematic payload {sha256}."
            raise RuntimeError(msg)
        return data

    async def _write_verified_object(self, digest: str, object_key: str, data: bytes) -> None:
        metadata = await self._artifacts.put(object_key, data, content_type="application/octet-stream")
        if metadata.byte_size != len(data) or metadata.sha256 not in (None, digest):
            msg = f"Object storage did not confirm schematic payload {digest}."
            raise RuntimeError(msg)
        stored = await self._artifacts.get(object_key, max_bytes=len(data))
        if stored is None or stored != data or hashlib.sha256(stored).hexdigest() != digest:
            msg = f"Object storage failed integrity verification for schematic payload {digest}."
            raise RuntimeError(msg)

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
        """Attach an analysis to a build, replacing any earlier analysis of the same file.

        Re-analysing after an engine upgrade must overwrite rather than accumulate, so the
        `(build_id, file_sha256)` uniqueness doubles as the upsert key.
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
                sanitized_at=publication.sanitized_at,
                sanitizer_version=publication.sanitizer_version,
                sanitization_report=publication.sanitization_report,
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
        async with self._session_factory() as session:
            if primary:
                await session.scalar(select(_BUILD_TABLE.c.id).where(_BUILD_TABLE.c.id == build_id).with_for_update())
                # The partial unique index allows only one primary per build, so the previous
                # holder must be demoted in the same transaction as the new one is promoted.
                await session.execute(
                    update(BuildSchematic)
                    .where(and_(BuildSchematic.build_id == build_id, BuildSchematic.file_sha256 != sha256))
                    .values(is_primary=False)
                )
            schematic_id = await session.scalar(statement)
            if primary:
                await self._clear_projected_render(session, build_id)
                render_job = insert(SchematicRenderQueueItem).values(build_id=build_id)
                await session.execute(
                    render_job.on_conflict_do_update(
                        index_elements=[SchematicRenderQueueItem.build_id],
                        set_={
                            "enqueued_at": func.now(),
                            "available_at": func.now(),
                            "claimed_at": None,
                            "claim_token": None,
                            "dead_at": None,
                            "attempts": 0,
                            "last_error": None,
                        },
                    )
                )
            await session.commit()
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

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SchematicRender).where(
                    and_(
                        SchematicRender.build_schematic_id == schematic_id,
                        SchematicRender.recipe_hash == recipe_hash,
                    )
                )
            )
        return _stored_render(row) if row is not None else None

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
        """Publish one render only if its source remains the build's primary schematic.

        Primary replacement takes the same build-row lock before changing the attachment.
        Whichever transaction wins therefore leaves a coherent result: either this URL is
        projected while its source is current, or replacement clears/rejects it.
        """
        statement = (
            insert(SchematicRender)
            .values(
                build_schematic_id=schematic_id,
                recipe_hash=recipe_hash,
                url=url,
                object_key=object_key,
                width=width,
                height=height,
                byte_size=byte_size,
            )
            .on_conflict_do_update(
                index_elements=[SchematicRender.build_schematic_id, SchematicRender.recipe_hash],
                set_={
                    "url": url,
                    "object_key": object_key,
                    "width": width,
                    "height": height,
                    "byte_size": byte_size,
                },
            )
            .returning(SchematicRender)
        )
        async with self._session_factory() as session:
            build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
            if build_id is None:
                return None
            await session.scalar(select(_BUILD_TABLE.c.id).where(_BUILD_TABLE.c.id == build_id).with_for_update())
            current_id = await session.scalar(
                select(BuildSchematic.id).where(
                    BuildSchematic.build_id == build_id,
                    BuildSchematic.is_primary,
                )
            )
            if current_id != schematic_id:
                return None
            row = await session.scalar(statement)
            await self._replace_projected_render(session, build_id, url)
            await session.commit()
        assert row is not None
        return _stored_render(row)

    async def project_render(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Project an existing recipe only if its source remains primary."""
        async with self._session_factory() as session:
            build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
            if build_id is None:
                return False
            await session.scalar(select(_BUILD_TABLE.c.id).where(_BUILD_TABLE.c.id == build_id).with_for_update())
            current_id = await session.scalar(
                select(BuildSchematic.id).where(
                    BuildSchematic.build_id == build_id,
                    BuildSchematic.is_primary,
                )
            )
            if current_id != schematic_id:
                return False
            render_exists = await session.scalar(
                select(SchematicRender.id).where(
                    SchematicRender.build_schematic_id == schematic_id,
                    SchematicRender.recipe_hash == recipe_hash,
                    SchematicRender.url == url,
                )
            )
            if render_exists is None:
                return False
            await self._replace_projected_render(session, build_id, url)
            await session.commit()
        return True

    @staticmethod
    async def _replace_projected_render(session: AsyncSession, build_id: int, url: str) -> None:
        registered_urls = SchematicRepository._registered_render_urls(build_id)
        existing = tuple(
            await session.scalars(
                select(BuildLink.url).where(
                    BuildLink.build_id == build_id,
                    BuildLink.media_type == "render",
                    BuildLink.url.in_(registered_urls),
                )
            )
        )
        if existing == (url,):
            return
        await session.execute(
            delete(BuildLink).where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
            )
        )
        await session.execute(insert(BuildLink).values(build_id=build_id, url=url, media_type="render"))
        await session.execute(
            update(_BUILD_TABLE).where(_BUILD_TABLE.c.id == build_id).values(revision=_BUILD_TABLE.c.revision + 1)
        )

    @staticmethod
    async def _clear_projected_render(session: AsyncSession, build_id: int) -> None:
        registered_urls = SchematicRepository._registered_render_urls(build_id)
        existing = await session.scalar(
            select(BuildLink.url)
            .where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
            )
            .limit(1)
        )
        if existing is None:
            return
        await session.execute(
            delete(BuildLink).where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
            )
        )
        await session.execute(
            update(_BUILD_TABLE).where(_BUILD_TABLE.c.id == build_id).values(revision=_BUILD_TABLE.c.revision + 1)
        )

    @staticmethod
    def _registered_render_urls(build_id: int) -> Select[tuple[str]]:
        """Return URLs whose lifecycle is owned by this schematic projection."""
        return (
            select(SchematicRender.url)
            .join(BuildSchematic, SchematicRender.build_schematic_id == BuildSchematic.id)
            .where(BuildSchematic.build_id == build_id)
        )

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None:
        """Load a registered preview artifact within the API response budget."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SchematicRender.object_key, SchematicRender.byte_size).where(
                        SchematicRender.recipe_hash == recipe_hash
                    )
                )
            ).first()
        if row is None or row.object_key is None or row.byte_size > max_bytes:
            return None
        return await self._artifacts.get(row.object_key, max_bytes=max_bytes)

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        """Replace the latest moderator-triggered evidence for one schematic."""
        async with self._session_factory() as session:
            await session.execute(
                update(BuildSchematic)
                .where(BuildSchematic.id == schematic_id)
                .values(simulation_evidence=simulation_to_json(result))
            )
            await session.commit()

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


def _stored_render(row: SchematicRender) -> StoredRender:
    return StoredRender(
        schematic_id=row.build_schematic_id,
        recipe_hash=row.recipe_hash,
        url=row.url,
        width=row.width,
        height=row.height,
        byte_size=row.byte_size,
    )


def _dimension_within(value: int, span: int) -> ColumnElement[bool]:
    """Match rows any of whose extents is within `span` of `value`."""
    return or_(
        BuildSchematic.width.between(value - span, value + span),
        BuildSchematic.height.between(value - span, value + span),
        BuildSchematic.length.between(value - span, value + span),
    )


def _schematic_object_key(digest: str) -> str:
    return f"schematics/{digest[:2]}/{digest}"
