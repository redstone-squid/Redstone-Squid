"""Preview metadata transactions around object storage operations."""

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import anyio
from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.schematics.application.previews import PreviewObjectReservation, StoredRender
from squid.schematics.models import SchematicPreviewObject
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class PreviewCleanupClaim:
    """An object reserved for deletion, with the token required to finish or release that claim."""

    object_key: str
    token: UUID


class PreviewCleanupInProgressError(InvalidStateError):
    """An object key is reserved for deletion and cannot yet be reused."""

    def __init__(self, object_key: str) -> None:
        super().__init__(
            "Preview cleanup is in progress; retry publication after cleanup finishes.",
            context={"object_key": object_key},
        )


class PreviewMetadataRepository(Protocol):
    """Preview publication and cleanup operations within the caller-owned transaction."""

    async def get_render(
        self, schematic_id: int, recipe_hash: str
    ) -> tuple[StoredRender, SchematicPreviewObject] | None: ...
    async def reserve_preview_object(
        self, object_key: str, *, byte_size: int, sha256: str
    ) -> SchematicPreviewObject: ...
    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None: ...
    async def publish_fresh_preview(
        self, schematic_id: int, recipe_hash: str, url: str, object_key: str, *, width: int, height: int, byte_size: int
    ) -> StoredRender | None: ...
    async def publish_cached_preview(self, schematic_id: int, recipe_hash: str, url: str) -> bool: ...
    async def render_object(self, recipe_hash: str) -> SchematicPreviewObject | None: ...
    async def claim_cleanup(self, *, older_than: Instant) -> PreviewCleanupClaim | None: ...
    async def complete_cleanup(self, claim: PreviewCleanupClaim) -> bool: ...
    async def release_cleanup(self, claim: PreviewCleanupClaim) -> None: ...
    async def mark_object_not_ready(self, object_key: str) -> None: ...


class SchematicPreviewStorage:
    """Track stored preview images and the build links that publish them.

    An upload reservation records the expected size and digest before SchematicPreviewWorker writes
    the image. After the worker verifies the upload, it marks the object ready and asks to publish
    its URL. Cached previews can skip the upload when their stored metadata still matches.

    Storage operations run outside database transactions. Reservations and cleanup claims are
    committed first so competing workers see them. Cleanup removes metadata only if its claim token
    still matches after deleting the bytes.
    """

    def __init__(self, artifacts: ArtifactStore, *, uow_factory: UnitOfWorkFactory[PreviewUnitOfWork]) -> None:
        self._artifacts = artifacts
        self._uow_factory = uow_factory

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None:
        """Return a ready preview whose storage metadata still matches, or None.

        This checks object size and any digest supplied by storage; it does not download the content.
        """
        async with self._uow_factory() as uow:
            retained = await uow.schematic_previews.get_render(schematic_id, recipe_hash)
        if retained is None:
            return None
        render, row = retained
        if not await self._stored_object_is_ready(row):
            await self._mark_object_not_ready(row.object_key)
            return None
        return render

    async def reserve_preview_object(self, object_key: str, *, byte_size: int, sha256: str) -> PreviewObjectReservation:
        async with self._uow_factory() as uow:
            row = await uow.schematic_previews.reserve_preview_object(object_key, byte_size=byte_size, sha256=sha256)
            await uow.commit()
        if row.ready_at is not None and await self._stored_object_is_ready(row):
            return PreviewObjectReservation(object_key, byte_size, sha256, upload_required=False)
        if row.ready_at is not None:
            await self._mark_object_not_ready(object_key)
        return PreviewObjectReservation(object_key, byte_size, sha256, upload_required=True)

    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None:
        """Record readiness after the caller has uploaded and verified the reserved object."""
        async with self._uow_factory() as uow:
            await uow.schematic_previews.mark_preview_object_ready(reservation)
            await uow.commit()

    async def publish_fresh_preview(
        self, schematic_id: int, recipe_hash: str, url: str, object_key: str, *, width: int, height: int, byte_size: int
    ) -> StoredRender | None:
        async with self._uow_factory() as uow:
            result = await uow.schematic_previews.publish_fresh_preview(
                schematic_id, recipe_hash, url, object_key, width=width, height=height, byte_size=byte_size
            )
            await uow.commit()
            return result

    async def publish_cached_preview(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        async with self._uow_factory() as uow:
            retained = await uow.schematic_previews.get_render(schematic_id, recipe_hash)
        if retained is None or retained[0].url != url:
            return False
        if not await self._stored_object_is_ready(retained[1]):
            await self._mark_object_not_ready(retained[1].object_key)
            msg = "A cached schematic preview disappeared after it was selected for publication."
            raise DataIntegrityError(msg, context={"object_key": retained[1].object_key})
        async with self._uow_factory() as uow:
            applied = await uow.schematic_previews.publish_cached_preview(schematic_id, recipe_hash, url)
            await uow.commit()
            return applied

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None:
        """Read a ready preview within max_bytes and verify its size and recorded digest.

        Return None for an absent, unready, oversized, or corrupt preview. Missing or corrupt bytes
        also clear the object's ready marker.
        """
        async with self._uow_factory() as uow:
            row = await uow.schematic_previews.render_object(recipe_hash)
        if row is None or row.ready_at is None or row.byte_size > max_bytes:
            return None
        content = await self._artifacts.get(row.object_key, max_bytes=max_bytes)
        if (
            content is None
            or len(content) != row.byte_size
            or (row.sha256 is not None and hashlib.sha256(content).hexdigest() != row.sha256)
        ):
            await self._mark_object_not_ready(row.object_key)
            return None
        return content

    async def cleanup_unreferenced_preview_objects(self, *, older_than: Instant, limit: int) -> int:
        """Delete up to limit old, unreferenced objects and return the number removed.

        Ordinary deletion failures release the claim and propagate. Cancellation can leave the claim
        reserved until its lease expires.
        """
        removed = 0
        for _ in range(limit):
            async with self._uow_factory() as uow:
                claim = await uow.schematic_previews.claim_cleanup(older_than=older_than)
                await uow.commit()
            if claim is None:
                break
            try:
                with anyio.fail_after(60):
                    await self._artifacts.delete(claim.object_key)
            except Exception:
                async with self._uow_factory() as uow:
                    await uow.schematic_previews.release_cleanup(claim)
                    await uow.commit()
                raise
            async with self._uow_factory() as uow:
                removed += int(await uow.schematic_previews.complete_cleanup(claim))
                await uow.commit()
        return removed

    async def _stored_object_is_ready(self, row: SchematicPreviewObject) -> bool:
        metadata = await self._artifacts.stat(row.object_key)
        return metadata is not None and metadata.byte_size == row.byte_size and metadata.sha256 in (None, row.sha256)

    async def _mark_object_not_ready(self, object_key: str) -> None:
        async with self._uow_factory() as uow:
            await uow.schematic_previews.mark_object_not_ready(object_key)
            await uow.commit()


class PreviewUnitOfWork(UnitOfWork, Protocol):
    @property
    def schematic_previews(self) -> PreviewMetadataRepository: ...
