"""Authorize and register image or video uploads for submission drafts.

The request handler first obtains DraftUploadAuthority, then writes the upload
to a temporary file represented by StagedUpload. Registration checks access
again and passes both to the media job service. The draft revision captured
before upload prevents registration against a draft changed in the meantime.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from squid.core.actors import ActingContext
from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.core.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaProcessingJobService,
    StagedMediaUploadSubmission,
    StoredMediaArtifact,
)
from squid.core.media.domain import MediaKind, MediaLimits
from squid.core.media.domain.jobs import MediaArtifactRole, MediaJobStatus
from squid.core.media.errors import DraftMediaConflictError, DraftMediaNotFoundError, MediaUploadConflictError
from squid.core.submissions.application.drafts import StoredDraft, SubmissionDraftService

logger = logging.getLogger(__name__)


class DraftAttachmentJobs(Protocol):
    """Media job operations used behind the draft attachment boundary."""

    @property
    def limits(self) -> MediaLimits: ...

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        context: ActingContext,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID: ...

    async def get(self, upload_id: UUID, *, context: ActingContext) -> MediaJobSnapshot | None: ...

    async def list_for_draft(self, draft_id: UUID, *, context: ActingContext) -> Sequence[MediaJobSnapshot]: ...

    async def discard(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> bool: ...

    async def download(self, artifact: StoredMediaArtifact, destination: Path) -> bool: ...

    async def attach(self, source_id: UUID, target_id: UUID, upload_id: UUID, *, context: ActingContext) -> bool: ...


class DraftAttachmentOwnership(Protocol):
    """Resolve account-owned draft snapshots for attachment commands."""

    async def get_accessible(self, draft_id: UUID, /, *, context: ActingContext) -> StoredDraft: ...


@dataclass(slots=True)
class DraftUploadAuthority:
    """Permission to register one upload against a specific draft revision."""

    draft_id: UUID
    account_id: int
    draft_revision: int
    kind: MediaKind
    context: ActingContext
    _available: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or self.account_id < 1 or self.draft_revision < 0:
            msg = tr(t"submissions-draft-attachment-upload = Draft attachment upload authority is invalid.")
            raise ValidationError(msg)

    def consume(self) -> MediaDraftUploadAuthorization:
        """End this authority and return its persistence authorization exactly once."""
        if not self._available:
            msg = tr(
                t"submissions-attachment-upload-authority = The "
                t"draft attachment upload authority is no longer available."
            )
            raise InvalidStateError(msg)
        self._available = False
        return MediaDraftUploadAuthorization(
            owner_account_id=self.account_id,
            draft_revision=self.draft_revision,
        )


@dataclass(slots=True)
class StagedUpload:
    """A staged file that registration can consume once and then remove."""

    source_path: Path
    source_content_type: str
    _available: bool = field(default=True, init=False, repr=False)

    def consume(self) -> Path:
        """Return the temporary file's path once, transferring it to registration."""
        if not self._available:
            msg = tr(t"submissions-staged-attachment-unavailable = The staged attachment is no longer available.")
            raise InvalidStateError(msg)
        self._available = False
        return self.source_path

    def discard(self) -> None:
        """Invalidate this handle and remove the temporary file if it still exists."""
        self._available = False
        try:
            self.source_path.unlink(missing_ok=True)
        except OSError:
            # Registration may already be durable. Cleanup failure must not turn
            # that success into an ambiguous client-visible retry.
            logger.warning("Unable to remove a staged attachment", exc_info=True)


class DraftAttachmentService:
    """Authorize, register, inspect, and discard account-owned draft attachments."""

    def __init__(self, drafts: DraftAttachmentOwnership, jobs: DraftAttachmentJobs) -> None:
        self._drafts = drafts
        self._jobs = jobs

    @property
    def limits(self) -> MediaLimits:
        """Return the durable worker limits enforced for attachments."""
        return self._jobs.limits

    async def authorize_upload(
        self, draft_id: UUID, kind: MediaKind, *, context: ActingContext
    ) -> DraftUploadAuthority:
        """Issue revision-bound authority before a transport accepts request bytes."""
        with context.scope():
            draft = await self._drafts.get_accessible(draft_id, context=context)
            return DraftUploadAuthority(
                draft_id, draft.snapshot.owner_account_id, draft.snapshot.revision, kind, context
            )

    async def register(
        self,
        authority: DraftUploadAuthority,
        staged: StagedUpload,
        *,
        upload_id: UUID | None,
    ) -> MediaJobSnapshot:
        """Register the staged file after rechecking access and its draft revision.

        Each handle can be consumed only once. The staged file is removed on success or
        failure; cleanup errors are logged without replacing the registration result.
        """
        with authority.context.scope():
            try:
                await self._drafts.get_accessible(authority.draft_id, context=authority.context)
                registered_id = await self._jobs.submit_staged(
                    StagedMediaUploadSubmission(
                        draft_id=authority.draft_id,
                        kind=authority.kind,
                        source_path=staged.consume(),
                        source_content_type=staged.source_content_type,
                        upload_id=upload_id,
                    ),
                    authorization=authority.consume(),
                    context=authority.context,
                )
            except MediaUploadConflictError as error:
                raise DraftMediaConflictError(error.upload_id) from None
            finally:
                staged.discard()
            return await self._owned_snapshot(authority.draft_id, registered_id, authority.context)

    async def list(self, draft_id: UUID, *, context: ActingContext) -> Sequence[MediaJobSnapshot]:
        """List attachment jobs after checking owner or staff access."""
        with context.scope():
            await self._drafts.get_accessible(draft_id, context=context)
            return await self._jobs.list_for_draft(draft_id, context=context)

    async def get(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> MediaJobSnapshot:
        """Return an attachment after checking draft access and association."""
        with context.scope():
            await self._drafts.get_accessible(draft_id, context=context)
            return await self._owned_snapshot(draft_id, upload_id, context)

    async def download(
        self, draft_id: UUID, upload_id: UUID, role: MediaArtifactRole, destination: Path, *, context: ActingContext
    ) -> StoredMediaArtifact:
        """Copy one processed image, video, or thumbnail of an accessible draft's upload to disk.

        Raises DraftMediaNotFoundError unless processing completed with that artifact and the
        stored bytes still match their recorded digest.
        """
        if role not in {MediaArtifactRole.OUTPUT, MediaArtifactRole.VIDEO_THUMBNAIL}:
            raise DraftMediaNotFoundError(upload_id)
        snapshot = await self.get(draft_id, upload_id, context=context)
        artifacts = snapshot.artifacts if snapshot.status is MediaJobStatus.COMPLETED else ()
        artifact = next((item for item in artifacts if item.role is role), None)
        if artifact is None or not await self._jobs.download(artifact, destination):
            raise DraftMediaNotFoundError(upload_id)
        return artifact

    async def discard(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> None:
        """Discard an attachment after checking owner or staff access."""
        with context.scope():
            await self._drafts.get_accessible(draft_id, context=context)
            if not await self._jobs.discard(draft_id, upload_id, context=context):
                raise DraftMediaNotFoundError(upload_id)

    async def attach(
        self, source_id: UUID, target_id: UUID, upload_id: UUID, *, context: ActingContext
    ) -> MediaJobSnapshot:
        """Share a processed upload between accessible drafts without normalizing it again."""
        with context.scope():
            await self._drafts.get_accessible(source_id, context=context)
            await self._drafts.get_accessible(target_id, context=context)
            if not await self._jobs.attach(source_id, target_id, upload_id, context=context):
                raise DraftMediaNotFoundError(upload_id)
            return await self._owned_snapshot(target_id, upload_id, context)

    async def _owned_snapshot(self, draft_id: UUID, upload_id: UUID, context: ActingContext) -> MediaJobSnapshot:
        for snapshot in await self._jobs.list_for_draft(draft_id, context=context):
            if snapshot.upload.id == upload_id and snapshot.upload.draft_id == draft_id:
                return snapshot
        raise DraftMediaNotFoundError(upload_id)


def draft_attachment_service(
    drafts: SubmissionDraftService,
    jobs: MediaProcessingJobService | None,
) -> DraftAttachmentService | None:
    """Build the API attachment boundary only when media processing is enabled."""
    return None if jobs is None else DraftAttachmentService(drafts, jobs)
