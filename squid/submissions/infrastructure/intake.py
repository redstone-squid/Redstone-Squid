"""Persistence for supplied attachments that have not yet reached a processor."""

from collections.abc import Callable
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.intake import SuppliedAttachment, classify_supplied_file
from squid.submissions.domain import SubmissionAttentionIssue, SubmissionAttentionReason
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.infrastructure.schematics import _changed, _editable
from squid.submissions.models.entities import SubmissionDraft
from squid.submissions.models.intake import SubmissionSuppliedAttachment


class PostgresAttachmentIntake:
    """Keep failed or interrupted downloads visible to preparation and every draft renderer."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def reserve(self, draft: StoredDraft, attachment: SuppliedAttachment) -> None:
        session = self._session()
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
        session = self._session()
        rows = await session.scalars(
            select(SubmissionSuppliedAttachment)
            .where(SubmissionSuppliedAttachment.draft_id == draft_id)
            .order_by(SubmissionSuppliedAttachment.source_id)
        )
        retained = {
            row.source_id: SuppliedAttachment(row.source_id, row.filename, row.kind, row.status) for row in rows
        }
        draft = await session.get(SubmissionDraft, draft_id)
        if draft is not None:
            for source in TypeAdapter(tuple[SubmissionSourceFile, ...]).validate_python(draft.source_files):
                actual = retained.get(source.id)
                retained[source.id] = SuppliedAttachment(
                    source.id,
                    source.filename,
                    classify_supplied_file(source.filename, source.content_type),
                    actual.status if actual is not None else IntakeStatus.PENDING,
                    source,
                )
        return tuple(retained.values())

    async def set_status(self, draft_id: UUID, source_id: UUID, status: IntakeStatus) -> None:
        session = self._session()
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
        session = self._session()
        draft = await session.get(SubmissionDraft, draft_id)
        issues = (
            tuple(
                SubmissionAttentionIssue(field, SubmissionAttentionReason.WRONG_TYPE) for field in draft.source_issues
            )
            if draft is not None
            else ()
        )
        return issues + (
            (SubmissionAttentionIssue("attachments", SubmissionAttentionReason.MEDIA_REJECTED),) if unresolved else ()
        )
