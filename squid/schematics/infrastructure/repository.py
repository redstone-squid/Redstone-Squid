"""SQLAlchemy persistence adapter for stored schematics."""

import hashlib
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.artifacts import ArtifactStore
from squid.schematics.application.queries import StoredRender, StoredSchematic
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
        statement = (
            insert(SchematicFile)
            .values(
                sha256=digest,
                data=None,
                object_key=object_key,
                storage_state="pending",
                byte_size=len(data),
                source_format=source_format.value,
            )
            .on_conflict_do_nothing(index_elements=[SchematicFile.sha256])
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()
            state = await session.scalar(select(SchematicFile.storage_state).where(SchematicFile.sha256 == digest))
        if state != "ready":
            await self._store_and_verify(digest, object_key, data)
        return digest

    async def get_file(self, sha256: str) -> bytes | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        SchematicFile.data,
                        SchematicFile.object_key,
                        SchematicFile.byte_size,
                        SchematicFile.storage_state,
                    ).where(SchematicFile.sha256 == sha256)
                )
            ).one_or_none()
        if row is None or row.storage_state != "ready":
            return None
        if row.data is not None:
            return bytes(row.data)
        if row.object_key is None:
            return None
        data = await self._artifacts.get(row.object_key, max_bytes=row.byte_size)
        if data is None or len(data) != row.byte_size or hashlib.sha256(data).hexdigest() != sha256:
            msg = f"Object storage returned corrupt or missing schematic payload {sha256}."
            raise RuntimeError(msg)
        return data

    async def maintain_storage(self, *, limit: int = 20) -> tuple[int, int]:
        """Backfill legacy inline files and recover interrupted staged writes."""
        migrated = await self._migrate_legacy_files(limit=limit)
        recovered = await self._recover_staged_files(limit=limit)
        return migrated, recovered

    async def _migrate_legacy_files(self, *, limit: int) -> int:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(SchematicFile)
                        .where(SchematicFile.data.is_not(None), SchematicFile.object_key.is_(None))
                        .order_by(SchematicFile.created_at)
                        .limit(limit)
                    )
                ).all()
            )
        migrated = 0
        for row in rows:
            assert row.data is not None
            data = bytes(row.data)
            object_key = _schematic_object_key(row.sha256)
            await self._write_verified_object(row.sha256, object_key, data)
            async with self._session_factory.begin() as session:
                await session.execute(
                    update(SchematicFile)
                    .where(SchematicFile.sha256 == row.sha256, SchematicFile.data.is_not(None))
                    .values(
                        object_key=object_key,
                        storage_state="ready",
                        verified_at=func.now(),
                        data=None,
                    )
                )
            migrated += 1
        return migrated

    async def _recover_staged_files(self, *, limit: int) -> int:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(SchematicFile)
                        .where(
                            SchematicFile.storage_state.in_(("pending", "verified")),
                            SchematicFile.object_key.is_not(None),
                        )
                        .order_by(SchematicFile.created_at)
                        .limit(limit)
                    )
                ).all()
            )
        recovered = 0
        for row in rows:
            assert row.object_key is not None
            data = await self._artifacts.get(row.object_key, max_bytes=row.byte_size)
            if data is None:
                continue
            await self._write_verified_object(row.sha256, row.object_key, data)
            async with self._session_factory.begin() as session:
                await session.execute(
                    update(SchematicFile)
                    .where(SchematicFile.sha256 == row.sha256)
                    .values(storage_state="ready", verified_at=func.now())
                )
            recovered += 1
        return recovered

    async def _store_and_verify(self, digest: str, object_key: str, data: bytes) -> None:
        await self._write_verified_object(digest, object_key, data)
        async with self._session_factory.begin() as session:
            await session.execute(
                update(SchematicFile)
                .where(SchematicFile.sha256 == digest)
                .values(storage_state="verified", verified_at=func.now())
            )
        async with self._session_factory.begin() as session:
            await session.execute(
                update(SchematicFile)
                .where(SchematicFile.sha256 == digest, SchematicFile.storage_state == "verified")
                .values(storage_state="ready")
            )

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
            if primary:
                render_job = insert(SchematicRenderQueueItem).values(build_id=build_id)
                await session.execute(
                    render_job.on_conflict_do_update(
                        index_elements=[SchematicRenderQueueItem.build_id],
                        set_={
                            "enqueued_at": func.now(),
                            "claimed_at": None,
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
    ) -> StoredRender:
        """Upsert one derived preview so concurrent requests converge on one recipe row."""
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
            row = await session.scalar(statement)
            await session.commit()
        assert row is not None
        return _stored_render(row)

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
