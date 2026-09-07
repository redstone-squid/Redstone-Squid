"""Shared upload association and retention predicates."""

from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from squid.media.infrastructure.models import MediaDraftReference, MediaUploadRecord
from squid.submissions.domain import DraftStatus


def media_for_draft(draft_id: UUID, *, include_discarded: bool = False) -> ColumnElement[bool]:
    """Read references, retaining compatibility with pre-reference upload metadata."""
    linked = select(MediaDraftReference.upload_id).where(
        MediaDraftReference.upload_id == MediaUploadRecord.id,
        MediaDraftReference.draft_id == draft_id,
    )
    if not include_discarded:
        linked = linked.where(MediaDraftReference.discarded.is_(False))
    return or_(
        exists(linked),
        and_(
            MediaUploadRecord.draft_id == draft_id,
            ~exists(select(MediaDraftReference.upload_id).where(MediaDraftReference.upload_id == MediaUploadRecord.id)),
        ),
    )


def retained_elsewhere(draft_id: UUID) -> ColumnElement[bool]:
    """Keep a job while another active or submitted draft retains its file."""
    from squid.submissions.infrastructure.models import SubmissionDraft

    return exists(
        select(MediaDraftReference.upload_id)
        .join(SubmissionDraft, SubmissionDraft.id == MediaDraftReference.draft_id)
        .where(
            MediaDraftReference.upload_id == MediaUploadRecord.id,
            MediaDraftReference.draft_id != draft_id,
            MediaDraftReference.discarded.is_(False),
            (SubmissionDraft.status == DraftStatus.SUBMITTED)
            | ((SubmissionDraft.status != DraftStatus.EXPIRED) & (SubmissionDraft.expires_at > func.now())),
        )
    )
