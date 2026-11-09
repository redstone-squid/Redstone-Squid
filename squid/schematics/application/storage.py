"""Schematic file storage with application-owned metadata transactions."""

import hashlib
from typing import Protocol

from squid.artifacts import ArtifactStore
from squid.builds.history import BuildChange
from squid.core.errors import DataIntegrityError
from squid.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicAnalysis,
    SchematicFormat,
    SchematicMetrics,
    SimulationResult,
)
from squid.schematics.models import SchematicFile
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class SchematicMetadataRepository(Protocol):
    """Schematic metadata operations within the caller-owned transaction."""

    async def register_file(self, file: SchematicFile) -> None: ...
    async def get_file_metadata(self, sha256: str) -> SchematicFile | None: ...
    async def record_analysis(
        self,
        build_id: int,
        sha256: str,
        analysis: SchematicAnalysis,
        *,
        primary: bool,
        change: BuildChange,
        original_filename: str | None = None,
        uploaded_by_account_id: int | None = None,
        publication: SchematicPublication | None = None,
    ) -> int: ...
    async def list_for_build(self, build_id: int) -> list[StoredSchematic]: ...
    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None: ...
    async def get_featured(self, build_id: int) -> StoredSchematic | None: ...
    async def find_file_matches(
        self, sha256: str, *, exclude_build_id: int | None = None, limit: int = 25
    ) -> list[StoredSchematic]: ...
    async def find_fingerprint_matches(
        self,
        fingerprint: str,
        *,
        preset: FingerprintPreset,
        analyzer_version: str,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]: ...
    async def find_metric_neighbours(
        self, metrics: SchematicMetrics, *, tolerance: float, limit: int = 25, exclude_build_id: int | None = None
    ) -> list[StoredSchematic]: ...
    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None: ...


class SchematicStorageService:
    """Store schematic bytes separately from metadata transactions.

    Uploads are read back and verified before their metadata is committed. If that transaction
    fails, the content-addressed object can remain in storage for a later upload to reuse.
    """

    def __init__(self, artifacts: ArtifactStore, *, uow_factory: UnitOfWorkFactory[SchematicStorageUnitOfWork]) -> None:
        self._artifacts = artifacts
        self._uow_factory = uow_factory

    async def put_file(self, data: bytes, *, source_format: SchematicFormat) -> str:
        """Verify content-addressed storage before committing its metadata."""
        digest = hashlib.sha256(data).hexdigest()
        object_key = f"schematics/{digest[:2]}/{digest}"
        await self._write_verified_object(digest, object_key, data)
        async with self._uow_factory() as uow:
            await uow.schematic_metadata.register_file(
                SchematicFile(
                    sha256=digest, object_key=object_key, byte_size=len(data), source_format=source_format.value
                )
            )
            await uow.commit()
        return digest

    async def get_file(self, sha256: str) -> bytes | None:
        """Read and verify registered bytes, or return None if the digest is unregistered.

        Missing or corrupt bytes for an existing metadata row raise DataIntegrityError.
        """
        async with self._uow_factory() as uow:
            row = await uow.schematic_metadata.get_file_metadata(sha256)
        if row is None:
            return None
        data = await self._artifacts.get(row.object_key, max_bytes=row.byte_size)
        if data is None or len(data) != row.byte_size or hashlib.sha256(data).hexdigest() != sha256:
            msg = f"Object storage returned corrupt or missing schematic payload {sha256}."
            raise DataIntegrityError(msg)
        return data

    async def _write_verified_object(self, digest: str, object_key: str, data: bytes) -> None:
        metadata = await self._artifacts.put(object_key, data, content_type="application/octet-stream")
        if metadata.byte_size != len(data) or metadata.sha256 not in (None, digest):
            msg = f"Object storage did not confirm schematic payload {digest}."
            raise DataIntegrityError(msg)
        stored = await self._artifacts.get(object_key, max_bytes=len(data))
        if stored is None or stored != data or hashlib.sha256(stored).hexdigest() != digest:
            msg = f"Object storage failed integrity verification for schematic payload {digest}."
            raise DataIntegrityError(msg)

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
        async with self._uow_factory() as uow:
            value = await uow.schematic_metadata.record_analysis(
                build_id,
                sha256,
                analysis,
                primary=primary,
                change=BuildChange(
                    operation="schematic",
                    source="schematic_attachment",
                    actor_account_id=uploaded_by_account_id,
                    actor_system="schematic_worker" if uploaded_by_account_id is None else None,
                ),
                original_filename=original_filename,
                uploaded_by_account_id=uploaded_by_account_id,
                publication=publication,
            )
            await uow.commit()
            return value

    async def list_for_build(self, build_id: int) -> list[StoredSchematic]:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.list_for_build(build_id)

    async def get_for_build(self, build_id: int, schematic_id: int) -> StoredSchematic | None:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.get_for_build(build_id, schematic_id)

    async def get_featured(self, build_id: int) -> StoredSchematic | None:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.get_featured(build_id)

    async def find_file_matches(
        self, sha256: str, *, exclude_build_id: int | None = None, limit: int = 25
    ) -> list[StoredSchematic]:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.find_file_matches(
                sha256, exclude_build_id=exclude_build_id, limit=limit
            )

    async def find_fingerprint_matches(
        self,
        fingerprint: str,
        *,
        preset: FingerprintPreset,
        analyzer_version: str,
        exclude_build_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredSchematic]:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.find_fingerprint_matches(
                fingerprint,
                preset=preset,
                analyzer_version=analyzer_version,
                exclude_build_id=exclude_build_id,
                limit=limit,
            )

    async def find_metric_neighbours(
        self, metrics: SchematicMetrics, *, tolerance: float, limit: int = 25, exclude_build_id: int | None = None
    ) -> list[StoredSchematic]:
        async with self._uow_factory() as uow:
            return await uow.schematic_metadata.find_metric_neighbours(
                metrics, tolerance=tolerance, limit=limit, exclude_build_id=exclude_build_id
            )

    async def record_simulation(self, schematic_id: int, result: SimulationResult) -> None:
        async with self._uow_factory() as uow:
            await uow.schematic_metadata.record_simulation(schematic_id, result)
            await uow.commit()


class SchematicStorageUnitOfWork(UnitOfWork, Protocol):
    @property
    def schematic_metadata(self) -> SchematicMetadataRepository: ...
