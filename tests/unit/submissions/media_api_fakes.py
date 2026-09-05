"""Typed fakes shared by submission media route acceptance tests."""

import stat
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from whenever import Instant

from squid.api.dependencies import get_draft_attachments
from squid.api.errors import register_exception_handlers
from squid.api.security import Caller, current_caller
from squid.api.v1.submission_media import router
from squid.api.v1.submissions import authenticated_account
from squid.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaUploadMetadata,
    StagedMediaUploadSubmission,
    StoredMediaArtifact,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.submissions.application import DraftAttachmentService, StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.errors import DraftAccessDeniedError

ACCOUNT_ID = 42
DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
OTHER_UPLOAD_ID = UUID("eca19583-1409-43a3-b1f9-fbc73076cc40")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")


def stored_draft(draft_id: UUID = DRAFT_ID) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=draft_id,
            owner_account_id=ACCOUNT_ID,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="door",
        ),
        origin=SubmissionOrigin.WEB,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )


def snapshot(
    *,
    upload_id: UUID = UPLOAD_ID,
    draft_id: UUID = DRAFT_ID,
    kind: MediaKind = MediaKind.VIDEO,
    status: MediaJobStatus = MediaJobStatus.PENDING,
    artifacts: tuple[StoredMediaArtifact, ...] = (),
) -> MediaJobSnapshot:
    return MediaJobSnapshot(
        upload=MediaUploadMetadata(
            id=upload_id,
            draft_id=draft_id,
            kind=kind,
            source_content_type=f"{kind.value}/mp4" if kind is MediaKind.VIDEO else "image/png",
            source_byte_size=4,
            source_sha256="a" * 64,
            source_object_key=f"private/raw/{upload_id}",
            strip_audio=kind is MediaKind.VIDEO,
            created_at=NOW,
        ),
        status=status,
        attempts=0,
        available_at=NOW,
        claimed_at=None,
        claim_token=None,
        completed_at=NOW if status is MediaJobStatus.COMPLETED else None,
        dead_at=NOW if status is MediaJobStatus.DEAD else None,
        discarded_at=NOW if status is MediaJobStatus.DISCARDED else None,
        last_error="private decoder detail" if status is MediaJobStatus.DEAD else None,
        artifacts=artifacts,
    )


class FakeDrafts:
    def __init__(self, events: list[str], *, deny: bool = False) -> None:
        self.events = events
        self.deny = deny

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        self.events.append("owner")
        assert draft_id == DRAFT_ID
        assert account_id == ACCOUNT_ID
        if self.deny:
            raise DraftAccessDeniedError
        return stored_draft(draft_id)


class FakeMedia:
    def __init__(self, events: list[str], *, failure: BaseException | None = None) -> None:
        self.events = events
        self.limits = MediaLimits(
            max_images=2,
            max_videos=1,
            max_duration_milliseconds=1_000,
            max_source_bytes=8,
            max_output_bytes=16,
            max_pixels_per_frame=100,
            max_decoded_pixels_per_second=200,
        )
        self.failure = failure
        self.snapshots: dict[UUID, MediaJobSnapshot] = {}
        self.staged_path: Path | None = None
        self.staged_parent: Path | None = None
        self.staged_mode: int | None = None
        self.parent_mode: int | None = None
        self.staged_bytes: bytes | None = None
        self.submission: StagedMediaUploadSubmission | None = None
        self.discarded: tuple[UUID, UUID] | None = None

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        self.events.append("submit")
        assert authorization is not None
        assert authorization.owner_account_id == ACCOUNT_ID
        self.submission = submission
        self.staged_path = submission.source_path
        self.staged_parent = submission.source_path.parent
        self.staged_mode = stat.S_IMODE(submission.source_path.stat().st_mode)
        self.parent_mode = stat.S_IMODE(submission.source_path.parent.stat().st_mode)
        self.staged_bytes = submission.source_path.read_bytes()
        if self.failure is not None:
            raise self.failure
        upload_id = submission.upload_id or uuid4()
        self.snapshots[upload_id] = snapshot(
            upload_id=upload_id,
            draft_id=submission.draft_id,
            kind=submission.kind,
        )
        return upload_id

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return self.snapshots.get(upload_id)

    async def list_for_draft(self, draft_id: UUID) -> tuple[MediaJobSnapshot, ...]:
        return tuple(item for item in self.snapshots.values() if item.upload.draft_id == draft_id)

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        self.discarded = (draft_id, upload_id)
        current = self.snapshots.get(upload_id)
        if current is None or current.upload.draft_id != draft_id:
            return False
        self.snapshots[upload_id] = replace(
            current,
            status=MediaJobStatus.DISCARDED,
            completed_at=None,
            dead_at=None,
            discarded_at=NOW,
            artifacts=(),
        )
        return True


@dataclass(frozen=True, slots=True)
class DisabledMediaServices:
    media_jobs: None
    submission_drafts: FakeDrafts


@dataclass(frozen=True, slots=True)
class DisabledMediaRuntime:
    services: DisabledMediaServices


def app_with_fakes(media: FakeMedia, drafts: FakeDrafts) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def attachment_dependency() -> DraftAttachmentService:
        return DraftAttachmentService(drafts, media)

    async def account_dependency() -> int:
        return ACCOUNT_ID

    async def caller_dependency() -> Caller:
        return Caller(kind="account", subject=f"account:{ACCOUNT_ID}", account_id=ACCOUNT_ID)

    app.dependency_overrides[get_draft_attachments] = attachment_dependency
    app.dependency_overrides[authenticated_account] = account_dependency
    app.dependency_overrides[current_caller] = caller_dependency
    return app
