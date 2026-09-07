"""Persistence for supplied attachments that have not yet reached a processor."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.persistence.base import Base
from squid.persistence.types import StrEnumText
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.intake import IntakeStatus, SuppliedAttachment
from squid.submissions.domain import SubmissionAttentionIssue, SubmissionAttentionReason
from squid.submissions.infrastructure.schematics import _changed, _editable


class SubmissionSuppliedAttachment(Base, kw_only=True):
    """A supplied file retained before download and resolved only by registration or explicit discard."""

    __tablename__ = "submission_supplied_attachments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'discarded')", name="submission_supplied_attachments_status_check"
        ),
        CheckConstraint(
            "kind IN ('schematic', 'image', 'video', 'unknown')", name="submission_supplied_attachments_kind_check"
        ),
    )
    draft_id: Mapped[UUID] = mapped_column(
        SQLUUID(as_uuid=True), ForeignKey("submission_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[IntakeStatus] = mapped_column(StrEnumText(IntakeStatus))


class PostgresAttachmentIntake:
    """Keep failed or interrupted downloads visible to preparation and every draft renderer."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reserve(self, draft: StoredDraft, attachment: SuppliedAttachment) -> None:
        async with self._sessions.begin() as session:
            model = await _editable(session, draft)
            existing = await session.get(SubmissionSuppliedAttachment, (draft.snapshot.id, attachment.id))
            if existing is not None:
                if (existing.filename, existing.kind) != (attachment.filename, attachment.kind):
                    message = "This source identifier already belongs to another supplied file."
                    raise ConflictError(message)
                if existing.status is IntakeStatus.READY:
                    return
                if existing.status is IntakeStatus.DISCARDED:
                    message = "Discarded source identifiers cannot be reused; supply a new file identifier."
                    raise ConflictError(message)
                existing.status = IntakeStatus.PENDING
            else:
                retained = tuple(
                    await session.scalars(
                        select(SubmissionSuppliedAttachment).where(
                            SubmissionSuppliedAttachment.draft_id == draft.snapshot.id,
                            SubmissionSuppliedAttachment.status != IntakeStatus.DISCARDED,
                        )
                    )
                )
                if len(retained) >= 10:
                    message = "A submission can retain at most ten supplied files."
                    raise ValidationError(message)
                session.add(
                    SubmissionSuppliedAttachment(
                        draft_id=draft.snapshot.id,
                        source_id=attachment.id,
                        filename=attachment.filename,
                        kind=attachment.kind,
                        status=IntakeStatus.PENDING,
                    )
                )
            _changed(model)

    async def list(self, draft_id: UUID) -> tuple[SuppliedAttachment, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(SubmissionSuppliedAttachment)
                .where(SubmissionSuppliedAttachment.draft_id == draft_id)
                .order_by(SubmissionSuppliedAttachment.source_id)
            )
            return tuple(SuppliedAttachment(row.source_id, row.filename, row.kind, row.status) for row in rows)

    async def set_status(self, draft_id: UUID, source_id: UUID, status: IntakeStatus) -> None:
        async with self._sessions.begin() as session:
            model = await session.scalar(
                select(SubmissionSuppliedAttachment)
                .where(
                    SubmissionSuppliedAttachment.draft_id == draft_id,
                    SubmissionSuppliedAttachment.source_id == source_id,
                )
                .with_for_update()
            )
            if model is None:
                raise NotFoundError
            if model.status is IntakeStatus.DISCARDED:
                return
            if status is IntakeStatus.FAILED and model.status is IntakeStatus.READY:
                return
            model.status = status

    async def issues_for_draft(self, draft_id: UUID) -> tuple[SubmissionAttentionIssue, ...]:
        unresolved = [
            item for item in await self.list(draft_id) if item.status in {IntakeStatus.PENDING, IntakeStatus.FAILED}
        ]
        return (
            (SubmissionAttentionIssue("attachments", SubmissionAttentionReason.MEDIA_REJECTED),) if unresolved else ()
        )
