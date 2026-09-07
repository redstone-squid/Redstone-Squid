"""Private durable schematic intake while sanitization is unavailable."""

import hashlib
from typing import Protocol
from uuid import UUID, uuid4

import anyio

from squid.artifacts import ArtifactStore
from squid.core.errors import NotFoundError, ValidationError
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.application.drafts import DraftActor, StoredDraft, SubmissionDraftService, draft_actor_id
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState


class DraftSchematicRepository(Protocol):
    """Persist source identity and draft references before accepting storage side effects."""

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
        repository: DraftSchematicRepository,
        artifacts: ArtifactStore,
        *,
        max_bytes: int = SCHEMATIC_FILE_SCHEMA_MAX_BYTES,
    ) -> None:
        if not 0 < max_bytes <= SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
            message = "Schematic upload limit exceeds the supported range."
            raise ValueError(message)
        self._drafts = drafts
        self._repository = repository
        self._artifacts = artifacts
        self.max_bytes = max_bytes

    async def reserve(self, draft_id: UUID, actor: DraftActor, *, filename: str, upload_id: UUID) -> DraftSchematic:
        """Retain a source slot before downloading, so failed downloads remain actionable."""
        if not filename.strip() or len(filename) > 255 or upload_id.int == 0:
            message = "A schematic reservation needs a valid filename and identifier."
            raise ValidationError(message)
        draft = await self._drafts.get_accessible(draft_id, actor)
        return await self._repository.register(
            draft,
            DraftSchematic(upload_id, filename.strip(), None, None, DraftSchematicState.UPLOADING),
            draft_actor_id(actor),
        )

    async def fail(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Record a failed source download without dropping the attachment from the draft."""
        if not any(item.id == upload_id for item in await self.list(draft_id, actor)):
            raise NotFoundError
        await self._repository.finish_upload(upload_id, succeeded=False)

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
        draft = await self._drafts.get_accessible(draft_id, actor)
        source = DraftSchematic(
            upload_id or uuid4(),
            filename.strip(),
            hashlib.sha256(data).hexdigest(),
            len(data),
            DraftSchematicState.UPLOADING,
        )
        retained = await self._repository.register(draft, source, draft_actor_id(actor))
        if retained.state is not DraftSchematicState.WAITING_SANITIZER:
            try:
                # The cleanup grace period is longer than this bounded storage operation.
                with anyio.fail_after(60):
                    await self._artifacts.put(source.object_key, data, content_type="application/octet-stream")
            except Exception:
                await self._repository.finish_upload(source.id, succeeded=False)
                raise
            await self._repository.finish_upload(source.id, succeeded=True)
        return next(item for item in await self._repository.list(draft_id) if item.id == source.id)

    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[DraftSchematic, ...]:
        """Read private source metadata after checking current access."""
        await self._drafts.get_accessible(draft_id, actor)
        return await self._repository.list(draft_id)

    async def select_primary(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Choose one retained source explicitly when several schematics are attached."""
        await self._repository.select_primary(await self._drafts.get_accessible(draft_id, actor), upload_id)

    async def discard(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Remove one draft's reference while preserving references from other drafts."""
        await self._repository.discard(await self._drafts.get_accessible(draft_id, actor), upload_id)

    async def attach(self, source_id: UUID, destination_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        """Share a supplied file only between drafts the caller can currently access."""
        source = await self._drafts.get_accessible(source_id, actor)
        destination = await self._drafts.get_accessible(destination_id, actor)
        await self._repository.attach(source, destination, upload_id)

    async def cleanup(self, *, limit: int = 20) -> None:
        """Delete unreferenced quarantine objects, retaining failed deletions for the next pass."""
        for upload_id in await self._repository.cleanup_candidates(limit=limit):
            await self._artifacts.delete(f"submissions/quarantine/{upload_id}")
            await self._repository.mark_deleted(upload_id)
