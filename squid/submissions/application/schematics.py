"""Private durable schematic intake while sanitization is unavailable."""

import hashlib
from typing import Protocol
from uuid import UUID, uuid4

import anyio

from squid.artifacts import ArtifactStore
from squid.core.errors import InvalidStateError, NotFoundError, ValidationError
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.application.artifact_readiness import DraftSchematicSnapshot
from squid.submissions.application.drafts import (
    DraftActor,
    DraftUnitOfWork,
    StoredDraft,
    SubmissionDraftService,
    draft_actor_id,
)
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.unit_of_work import UnitOfWorkFactory


class DraftSchematicRepository(Protocol):
    """Persist source identity and draft references before accepting storage side effects."""

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot: ...

    async def register(self, draft: StoredDraft, source: DraftSchematic, actor_id: int) -> DraftSchematic: ...

    async def finish_upload(self, upload_id: UUID, *, succeeded: bool) -> None: ...

    async def list(self, draft_id: UUID) -> tuple[DraftSchematic, ...]: ...

    async def select_primary(self, draft: StoredDraft, upload_id: UUID) -> None: ...

    async def discard(self, draft: StoredDraft, upload_id: UUID) -> None: ...

    async def attach(self, source: StoredDraft, destination: StoredDraft, upload_id: UUID) -> None: ...

    async def cleanup_candidates(self, *, limit: int) -> tuple[UUID, ...]: ...

    async def mark_deleted(self, upload_id: UUID) -> None: ...


class DraftSchematicService:
    """Retain private sources and let their owners resolve selection and upload failures."""

    def __init__(
        self,
        drafts: SubmissionDraftService,
        artifacts: ArtifactStore,
        *,
        uow_factory: UnitOfWorkFactory[DraftSchematicUnitOfWork],
        max_bytes: int = SCHEMATIC_FILE_SCHEMA_MAX_BYTES,
    ) -> None:
        if not 0 < max_bytes <= SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
            message = "Schematic upload limit exceeds the supported range."
            raise InvalidStateError(message)
        self._drafts = drafts
        self._uow_factory = uow_factory
        self._artifacts = artifacts
        self.max_bytes = max_bytes

    async def reserve(self, draft_id: UUID, actor: DraftActor, *, filename: str, upload_id: UUID) -> DraftSchematic:
        """Retain a source slot before downloading, so failed downloads remain actionable."""
        async with self._uow_factory() as uow:
            if not filename.strip() or len(filename) > 255 or upload_id.int == 0:
                message = "A schematic reservation needs a valid filename and identifier."
                raise ValidationError(message)
            draft = await self._drafts.get_accessible_in(uow, draft_id, actor)
            result = await uow.draft_schematics.register(
                draft,
                DraftSchematic(upload_id, filename.strip(), None, None, DraftSchematicState.UPLOADING),
                draft_actor_id(actor),
            )
            await uow.commit()
            return result

    async def fail(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Record a failed source download without dropping the attachment from the draft."""
        async with self._uow_factory() as uow:
            await self._drafts.get_accessible_in(uow, draft_id, actor)
            if not any(item.id == upload_id for item in await uow.draft_schematics.list(draft_id)):
                raise NotFoundError
            await uow.draft_schematics.finish_upload(upload_id, succeeded=False)
            await uow.commit()

    async def upload(
        self,
        draft_id: UUID,
        actor: DraftActor,
        *,
        filename: str,
        data: bytes,
        upload_id: UUID | None = None,
    ) -> DraftSchematic:
        """Retain bounded source bytes; retrying the same ID requires the same content."""
        if (
            not 0 < len(data) <= self.max_bytes
            or not filename.strip()
            or len(filename) > 255
            or (upload_id is not None and upload_id.int == 0)
        ):
            message = "A schematic needs a filename and source bytes within the upload limit."
            raise ValidationError(message)
        source = DraftSchematic(
            upload_id or uuid4(),
            filename.strip(),
            hashlib.sha256(data).hexdigest(),
            len(data),
            DraftSchematicState.UPLOADING,
        )
        async with self._uow_factory() as uow:
            draft = await self._drafts.get_accessible_in(uow, draft_id, actor)
            retained = await uow.draft_schematics.register(draft, source, draft_actor_id(actor))
            await uow.commit()
        if retained.state is not DraftSchematicState.WAITING_SANITIZER:
            try:
                # The cleanup grace period is longer than this bounded storage operation.
                with anyio.fail_after(60):
                    await self._artifacts.put(source.object_key, data, content_type="application/octet-stream")
            except Exception:
                await self._finish_upload(source.id, succeeded=False)
                raise
            await self._finish_upload(source.id, succeeded=True)
        return next(item for item in await self.list(draft_id, actor) if item.id == source.id)

    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[DraftSchematic, ...]:
        """Read private source metadata after checking current access."""
        async with self._uow_factory() as uow:
            await self._drafts.get_accessible_in(uow, draft_id, actor)
            return await uow.draft_schematics.list(draft_id)

    async def select_primary(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Choose one retained source explicitly when several schematics are attached."""
        async with self._uow_factory() as uow:
            await uow.draft_schematics.select_primary(
                await self._drafts.get_accessible_in(uow, draft_id, actor), upload_id
            )
            await uow.commit()

    async def discard(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Remove one draft's reference while preserving references from other drafts."""
        async with self._uow_factory() as uow:
            await uow.draft_schematics.discard(await self._drafts.get_accessible_in(uow, draft_id, actor), upload_id)
            await uow.commit()

    async def attach(self, source_id: UUID, destination_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Share a supplied file only between drafts the caller can currently access."""
        async with self._uow_factory() as uow:
            source = await self._drafts.get_accessible_in(uow, source_id, actor)
            destination = await self._drafts.get_accessible_in(uow, destination_id, actor)
            await uow.draft_schematics.attach(source, destination, upload_id)
            await uow.commit()

    async def cleanup(self, *, limit: int = 20) -> None:
        """Delete unreferenced quarantine objects, retaining failed deletions for the next pass."""
        async with self._uow_factory() as uow:
            upload_ids = await uow.draft_schematics.cleanup_candidates(limit=limit)
            await uow.commit()
        for upload_id in upload_ids:
            await self._artifacts.delete(f"submissions/quarantine/{upload_id}")
            async with self._uow_factory() as uow:
                await uow.draft_schematics.mark_deleted(upload_id)
                await uow.commit()

    async def _finish_upload(self, upload_id: UUID, *, succeeded: bool) -> None:
        async with self._uow_factory() as uow:
            await uow.draft_schematics.finish_upload(upload_id, succeeded=succeeded)
            await uow.commit()

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot:
        """Read retained schematic state for submission preparation."""
        async with self._uow_factory() as uow:
            return await uow.draft_schematics.read_for_draft(draft_id)


class DraftSchematicUnitOfWork(DraftUnitOfWork, Protocol):
    @property
    def draft_schematics(self) -> DraftSchematicRepository: ...
