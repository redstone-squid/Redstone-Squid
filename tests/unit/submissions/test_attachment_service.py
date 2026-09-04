"""Application boundary tests for account-owned draft attachments."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from whenever import Instant

from squid.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaUploadMetadata,
    StagedMediaUploadSubmission,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.media.errors import DraftMediaConflictError, DraftMediaNotFoundError, MediaUploadConflictError
from squid.submissions.application import DraftAttachmentService, DraftUploadAuthority, StagedUpload, StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin

NOW = Instant.parse_iso("2026-08-31T12:00:00Z")
DRAFT_ID = UUID("11111111-1111-4111-8111-111111111111")
UPLOAD_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_DRAFT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _draft(*, revision: int = 4) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=7,
            schema_id="submission",
            schema_revision=2,
            category="door",
            revision=revision,
        ),
        origin=SubmissionOrigin.WEB,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=1, days_assumed_24h_ok=True),
    )


def _snapshot(*, draft_id: UUID = DRAFT_ID, upload_id: UUID = UPLOAD_ID) -> MediaJobSnapshot:
    return MediaJobSnapshot(
        upload=MediaUploadMetadata(
            id=upload_id,
            draft_id=draft_id,
            kind=MediaKind.IMAGE,
            source_content_type="image/png",
            source_byte_size=3,
            source_sha256="a" * 64,
            source_object_key=f"media/raw/{upload_id}/{'a' * 64}",
            strip_audio=False,
        ),
        status=MediaJobStatus.PENDING,
        attempts=0,
        available_at=NOW,
        claimed_at=None,
        claim_token=None,
        completed_at=None,
        dead_at=None,
        discarded_at=None,
        last_error=None,
    )


@dataclass
class FakeDrafts:
    draft: StoredDraft
    calls: list[tuple[UUID, int]]

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        self.calls.append((draft_id, account_id))
        return self.draft


class FakeJobs:
    limits = MediaLimits(max_source_bytes=100)

    def __init__(self) -> None:
        self.snapshots = {UPLOAD_ID: _snapshot()}
        self.submissions: list[tuple[StagedMediaUploadSubmission, MediaDraftUploadAuthorization | None]] = []
        self.discarded: list[tuple[UUID, UUID]] = []
        self.submit_error: Exception | None = None

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        self.submissions.append((submission, authorization))
        if self.submit_error is not None:
            raise self.submit_error
        return UPLOAD_ID

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return self.snapshots.get(upload_id)

    async def list_for_draft(self, draft_id: UUID) -> tuple[MediaJobSnapshot, ...]:
        return tuple(snapshot for snapshot in self.snapshots.values() if snapshot.upload.draft_id == draft_id)

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        self.discarded.append((draft_id, upload_id))
        return upload_id in self.snapshots and self.snapshots[upload_id].upload.draft_id == draft_id


async def test_authorize_then_register_revalidates_owner_and_revision_and_consumes_file(tmp_path: Path) -> None:
    drafts = FakeDrafts(_draft(revision=9), [])
    jobs = FakeJobs()
    service = DraftAttachmentService(drafts, jobs)
    authority = await service.authorize_upload(DRAFT_ID, 7, MediaKind.IMAGE)
    source = tmp_path / "source"
    source.write_bytes(b"png")

    snapshot = await service.register(
        authority,
        StagedUpload(source, "image/png"),
        strip_audio=False,
        upload_id=UPLOAD_ID,
    )

    assert snapshot.upload.id == UPLOAD_ID
    assert drafts.calls == [(DRAFT_ID, 7)]
    submission, authorization = jobs.submissions[0]
    assert submission.draft_id == DRAFT_ID
    assert submission.kind is MediaKind.IMAGE
    assert authorization == MediaDraftUploadAuthorization(owner_account_id=7, draft_revision=9)
    assert not source.exists()


async def test_register_cleans_staged_bytes_and_localizes_private_upload_conflicts(tmp_path: Path) -> None:
    jobs = FakeJobs()
    jobs.submit_error = MediaUploadConflictError(
        UPLOAD_ID,
        existing_source_object_key="media/raw/private-object-key",
        existing_status=MediaJobStatus.PENDING,
    )
    service = DraftAttachmentService(FakeDrafts(_draft(), []), jobs)
    source = tmp_path / "source"
    source.write_bytes(b"png")

    with pytest.raises(DraftMediaConflictError) as raised:
        await service.register(
            DraftUploadAuthority(DRAFT_ID, 7, 4, MediaKind.IMAGE),
            StagedUpload(source, "image/png"),
            strip_audio=False,
            upload_id=UPLOAD_ID,
        )

    assert raised.value.public_context == {"upload_id": str(UPLOAD_ID)}
    assert "private-object-key" not in str(raised.value.public_context)
    assert not source.exists()


async def test_register_rejects_reused_staging_authority(tmp_path: Path) -> None:
    service = DraftAttachmentService(FakeDrafts(_draft(), []), FakeJobs())
    source = tmp_path / "source"
    source.write_bytes(b"png")
    staged = StagedUpload(source, "image/png")
    authority = DraftUploadAuthority(DRAFT_ID, 7, 4, MediaKind.IMAGE)
    await service.register(authority, staged, strip_audio=False, upload_id=UPLOAD_ID)

    with pytest.raises(Exception, match="no longer available"):
        await service.register(authority, staged, strip_audio=False, upload_id=uuid4())


async def test_get_and_discard_enforce_ownership_then_draft_association() -> None:
    drafts = FakeDrafts(_draft(), [])
    jobs = FakeJobs()
    jobs.snapshots[UPLOAD_ID] = _snapshot(draft_id=OTHER_DRAFT_ID)
    service = DraftAttachmentService(drafts, jobs)

    with pytest.raises(DraftMediaNotFoundError):
        await service.get(DRAFT_ID, 7, UPLOAD_ID)
    with pytest.raises(DraftMediaNotFoundError):
        await service.discard(DRAFT_ID, 7, UPLOAD_ID)

    assert drafts.calls == [(DRAFT_ID, 7), (DRAFT_ID, 7)]
    assert jobs.discarded == [(DRAFT_ID, UPLOAD_ID)]
