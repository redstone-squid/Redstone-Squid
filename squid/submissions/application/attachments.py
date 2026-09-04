"""Application-owned draft attachment orchestration."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaNormalizationJobService,
    StagedMediaUploadSubmission,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.media.errors import DraftMediaConflictError, DraftMediaNotFoundError, MediaUploadConflictError
from squid.submissions.application.drafts import StoredDraft, SubmissionDraftService


class DraftAttachmentJobs(Protocol):
    """Media job operations used behind the draft attachment boundary."""

    @property
    def limits(self) -> MediaLimits: ...

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID: ...

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None: ...

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]: ...

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool: ...


class DraftAttachmentOwnership(Protocol):
    """Resolve account-owned draft snapshots for attachment commands."""

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft: ...


@dataclass(slots=True)
class DraftUploadAuthority:
    """Short-lived authority whose lifetime ends when registration consumes it."""

    draft_id: UUID
    account_id: int
    draft_revision: int
    kind: MediaKind
    _available: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or self.account_id < 1 or self.draft_revision < 0:
            msg = tr(t"Draft attachment upload authority is invalid.")
            raise ValidationError(msg)

    def consume(self) -> MediaDraftUploadAuthorization:
        """End this authority and return its persistence authorization exactly once."""
        if not self._available:
            msg = tr(t"The draft attachment upload authority is no longer available.")
            raise InvalidStateError(msg)
        self._available = False
        return MediaDraftUploadAuthorization(
            owner_account_id=self.account_id,
            draft_revision=self.draft_revision,
        )


@dataclass(slots=True)
class StagedUpload:
    """Private staged bytes whose authority ends when registered or discarded."""

    source_path: Path
    source_content_type: str
    _available: bool = field(default=True, init=False, repr=False)

    def consume(self) -> Path:
        """End this staging authority and return its path exactly once."""
        if not self._available:
            msg = tr(t"The staged attachment is no longer available.")
            raise InvalidStateError(msg)
        self._available = False
        return self.source_path

    def discard(self) -> None:
        """End this staging authority and remove any retained private bytes."""
        self._available = False
        self.source_path.unlink(missing_ok=True)


class DraftAttachmentService:
    """Authorize, register, inspect, and discard account-owned draft attachments."""

    def __init__(self, drafts: DraftAttachmentOwnership, jobs: DraftAttachmentJobs) -> None:
        self._drafts = drafts
        self._jobs = jobs

    @property
    def limits(self) -> MediaLimits:
        """Return the durable worker limits enforced for attachments."""
        return self._jobs.limits

    async def authorize_upload(self, draft_id: UUID, account_id: int, kind: MediaKind) -> DraftUploadAuthority:
        """Issue revision-bound authority before a transport accepts request bytes."""
        draft = await self._drafts.get_owned(draft_id, account_id)
        return DraftUploadAuthority(draft_id, account_id, draft.snapshot.revision, kind)

    async def register(
        self,
        authority: DraftUploadAuthority,
        staged: StagedUpload,
        *,
        strip_audio: bool,
        upload_id: UUID | None,
    ) -> MediaJobSnapshot:
        """Consume staged bytes and atomically revalidate their upload authority."""
        try:
            registered_id = await self._jobs.submit_staged(
                StagedMediaUploadSubmission(
                    draft_id=authority.draft_id,
                    kind=authority.kind,
                    source_path=staged.consume(),
                    source_content_type=staged.source_content_type,
                    strip_audio=strip_audio,
                    upload_id=upload_id,
                ),
                authorization=authority.consume(),
            )
        except MediaUploadConflictError as error:
            raise DraftMediaConflictError(error.upload_id) from None
        finally:
            staged.discard()
        return await self._owned_snapshot(authority.draft_id, registered_id)

    async def list(self, draft_id: UUID, account_id: int) -> Sequence[MediaJobSnapshot]:
        """List attachment jobs after enforcing current draft ownership."""
        await self._drafts.get_owned(draft_id, account_id)
        return await self._jobs.list_for_draft(draft_id)

    async def get(self, draft_id: UUID, account_id: int, upload_id: UUID) -> MediaJobSnapshot:
        """Return one attachment after enforcing ownership and draft association."""
        await self._drafts.get_owned(draft_id, account_id)
        return await self._owned_snapshot(draft_id, upload_id)

    async def discard(self, draft_id: UUID, account_id: int, upload_id: UUID) -> None:
        """Discard one attachment after enforcing current draft ownership."""
        await self._drafts.get_owned(draft_id, account_id)
        if not await self._jobs.discard(draft_id, upload_id):
            raise DraftMediaNotFoundError(upload_id)

    async def _owned_snapshot(self, draft_id: UUID, upload_id: UUID) -> MediaJobSnapshot:
        snapshot = await self._jobs.get(upload_id)
        if snapshot is None or snapshot.upload.draft_id != draft_id:
            raise DraftMediaNotFoundError(upload_id)
        return snapshot


def draft_attachment_service(
    drafts: SubmissionDraftService,
    jobs: MediaNormalizationJobService | None,
) -> DraftAttachmentService | None:
    """Build the API attachment boundary only when media processing is enabled."""
    return None if jobs is None else DraftAttachmentService(drafts, jobs)
