"""Which uploads belong to a draft, and which drafts keep an upload's file from cleanup."""

from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from squid.media.models import MediaDraftReference, MediaUploadRecord
from squid.submissions.domain import DraftStatus


def media_for_draft(draft_id: UUID, *, include_discarded: bool = False) -> ColumnElement[bool]:
    """Match uploads linked to a draft, including legacy uploads without references.

    The upload's original draft_id is used only when no reference rows exist for
    that upload. Once references exist, their discarded flags control membership.
    """
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


def draft_keeps_file(discarded: ColumnElement[bool] | InstrumentedAttribute[bool]) -> ColumnElement[bool]:
    """Whether a draft's reference to a file keeps the file from cleanup.

    Use it in a query joined to `submission_drafts`. `discarded` is the reference's flag
    for a file the draft has removed, or staged but not yet accepted. A draft keeps the
    files it lists while it is open and after submission. An open draft also keeps the
    files it has discarded, so session undo can restore them; they become deletable when
    the draft is submitted or expires.
    """
    from squid.submissions.models.entities import SubmissionDraft

    open_draft = SubmissionDraft.status.not_in((DraftStatus.SUBMITTED, DraftStatus.EXPIRED)) & (
        SubmissionDraft.expires_at > func.now()
    )
    return open_draft | ((SubmissionDraft.status == DraftStatus.SUBMITTED) & discarded.is_(False))


def retained_elsewhere(draft_id: UUID) -> ColumnElement[bool]:
    """Whether a draft other than `draft_id` keeps the upload's file (see `draft_keeps_file`)."""
    from squid.submissions.models.entities import SubmissionDraft

    return exists(
        select(MediaDraftReference.upload_id)
        .join(SubmissionDraft, SubmissionDraft.id == MediaDraftReference.draft_id)
        .where(
            MediaDraftReference.upload_id == MediaUploadRecord.id,
            MediaDraftReference.draft_id != draft_id,
            draft_keeps_file(MediaDraftReference.discarded),
        )
    )
