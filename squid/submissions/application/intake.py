"""Track every supplied file before downloading or processing it."""

import mimetypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaNormalizationJobService,
    StagedMediaUploadSubmission,
)
from squid.media.domain import MediaKind
from squid.schematics.domain.formats import SCHEMATIC_EXTENSIONS
from squid.submissions.application.drafts import DraftActor, StoredDraft, SubmissionDraftService
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.source_files import SubmissionSourceFile


class IntakeStatus(StrEnum):
    """State of a supplied file before and after durable processor registration."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class SuppliedAttachment:
    """One supplied file whose failure or omission requires explicit resolution."""

    id: UUID
    filename: str
    kind: str
    status: IntakeStatus
    source: SubmissionSourceFile | None = None


class AttachmentIntakeRepository(Protocol):
    """Persist intake intent independently of the eventual processor."""

    async def reserve(self, draft: StoredDraft, attachment: SuppliedAttachment) -> None: ...
    async def list(self, draft_id: UUID) -> tuple[SuppliedAttachment, ...]: ...
    async def set_status(self, draft_id: UUID, source_id: UUID, status: IntakeStatus) -> None: ...


class SubmissionAttachmentIntake:
    """Retain supplied-file intent so failed downloads cannot disappear from submission."""

    def __init__(
        self,
        drafts: SubmissionDraftService,
        repository: AttachmentIntakeRepository,
        schematics: DraftSchematicService,
        media: MediaNormalizationJobService | None,
    ) -> None:
        self._drafts = drafts
        self._repository = repository
        self._schematics = schematics
        self._media = media

    async def reserve(
        self, draft_id: UUID, actor: DraftActor, source_id: UUID, filename: str, content_type: str | None
    ) -> SuppliedAttachment:
        """Register intent before a transport performs its first download."""
        if source_id.int == 0 or not filename.strip() or len(filename) > 255:
            message = "A supplied attachment needs a valid identifier and filename."
            raise ValidationError(message)
        kind = classify_supplied_file(filename, content_type)
        attachment = SuppliedAttachment(source_id, filename, kind, IntakeStatus.PENDING)
        await self._repository.reserve(await self._drafts.get_accessible(draft_id, actor), attachment)
        return attachment

    async def register_file(
        self, draft_id: UUID, actor: DraftActor, source_id: UUID, path: Path, content_type: str | None
    ) -> None:
        """Register downloaded bytes with the existing durable processor and retain failures."""
        draft = await self._drafts.get_accessible(draft_id, actor)
        attachment = next((item for item in await self._repository.list(draft_id) if item.id == source_id), None)
        if attachment is None:
            raise NotFoundError
        if attachment.status is IntakeStatus.DISCARDED:
            message = "This supplied file was discarded; supply a new file identifier."
            raise ConflictError(message)
        if content_type in {None, "application/octet-stream"}:
            content_type = mimetypes.guess_type(attachment.filename)[0]
        try:
            await self._register(draft, actor, attachment, path, content_type)
        except Exception:
            await self._repository.set_status(draft_id, source_id, IntakeStatus.FAILED)
            raise
        await self._repository.set_status(draft_id, source_id, IntakeStatus.READY)

    async def _register(
        self,
        draft: StoredDraft,
        actor: DraftActor,
        attachment: SuppliedAttachment,
        path: Path,
        content_type: str | None,
    ) -> None:
        if attachment.kind == "schematic":
            if path.stat().st_size > self._schematics.max_bytes:
                message = "Schematic exceeds the upload limit."
                raise ValidationError(message)
            await self._schematics.upload(
                draft.snapshot.id, actor, filename=attachment.filename, data=path.read_bytes(), upload_id=attachment.id
            )
        elif attachment.kind in {"image", "video"} and self._media is not None:
            existing = await self._media.get(attachment.id)
            if existing is not None and existing.upload.draft_id != draft.snapshot.id:
                if not await self._media.attach(existing.upload.draft_id, draft.snapshot.id, attachment.id):
                    message = "This shared file is no longer available; upload a replacement."
                    raise ConflictError(message)
                return
            await self._media.submit_staged(
                StagedMediaUploadSubmission(
                    draft_id=draft.snapshot.id,
                    kind=MediaKind(attachment.kind),
                    source_path=path,
                    source_content_type=content_type
                    or mimetypes.guess_type(attachment.filename)[0]
                    or "application/octet-stream",
                    strip_audio=False,
                    upload_id=attachment.id,
                ),
                authorization=MediaDraftUploadAuthorization(
                    owner_account_id=draft.snapshot.owner_account_id, draft_revision=draft.snapshot.revision
                ),
            )
        else:
            message = "This file type cannot currently be processed; retry or explicitly discard it."
            raise ValidationError(message)

    async def fail(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        """Retain a download failure for explicit retry or discard."""
        await self._drafts.get_accessible(draft_id, actor)
        await self._repository.set_status(draft_id, source_id, IntakeStatus.FAILED)

    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        await self._drafts.get_accessible(draft_id, actor)
        return await self._repository.list(draft_id)

    async def discard(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        """Discard processor references before resolving the supplied-file requirement."""
        attachment = next((item for item in await self.list(draft_id, actor) if item.id == source_id), None)
        if attachment is None:
            raise NotFoundError
        if attachment.status is IntakeStatus.DISCARDED:
            return
        if attachment.source is not None:
            await self.reserve(draft_id, actor, source_id, attachment.filename, attachment.source.content_type)
        if attachment.kind == "schematic":
            if any(item.id == source_id for item in await self._schematics.list(draft_id, actor)):
                await self._schematics.discard(draft_id, actor, source_id)
        elif attachment.kind in {"image", "video"} and self._media is not None:
            await self._media.discard(draft_id, source_id)
        await self._repository.set_status(draft_id, source_id, IntakeStatus.DISCARDED)


def classify_supplied_file(filename: str, content_type: str | None) -> str:
    """Classify supplied-file intent without treating the filename as verified content."""
    suffix = Path(filename.lower()).suffix
    mime = (
        content_type
        if content_type not in {None, "application/octet-stream"}
        else mimetypes.guess_type(filename)[0] or ""
    )
    return (
        "schematic"
        if suffix in SCHEMATIC_EXTENSIONS
        else "image"
        if mime.startswith("image/")
        else "video"
        if mime.startswith("video/")
        else "unknown"
    )
