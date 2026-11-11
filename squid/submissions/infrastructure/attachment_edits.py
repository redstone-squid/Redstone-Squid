"""Change attachment references without discarding their shared processing results."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.media.domain import MediaBatchTotals, MediaKind, MediaLimits
from squid.media.domain.jobs import MediaArtifactRole, MediaJobStatus
from squid.media.errors import MediaLimitExceededError
from squid.media.infrastructure.references import media_for_draft
from squid.media.models import MediaArtifactRecord, MediaDraftReference, MediaNormalizationJobRecord, MediaUploadRecord
from squid.submissions.application.attachment_edits import AttachmentValues, FileMembership
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.domain.schematics import DraftSchematicState
from squid.submissions.infrastructure.intake import PostgresAttachmentIntake
from squid.submissions.infrastructure.schematics import _changed, _editable
from squid.submissions.models.intake import SubmissionSuppliedAttachment
from squid.submissions.models.schematics import DraftSchematicLink, DraftSchematicSource


class PostgresAttachmentEdits:
    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def read(self, draft: StoredDraft, sources: tuple[UUID, ...]) -> AttachmentValues:
        session = self._session()
        await _editable(session, draft)
        supplied = {item.id: item for item in await PostgresAttachmentIntake(self._session).list(draft.snapshot.id)}
        schematics = {
            row.source_id: row
            for row in await session.scalars(
                select(DraftSchematicLink).where(DraftSchematicLink.draft_id == draft.snapshot.id)
            )
        }
        media = set(await session.scalars(select(MediaUploadRecord.id).where(media_for_draft(draft.snapshot.id))))
        known_media = set(
            await session.scalars(
                select(MediaUploadRecord.id).where(media_for_draft(draft.snapshot.id, include_discarded=True))
            )
        )
        files = []
        for source in sources:
            if source in supplied:
                present = supplied[source].status is not IntakeStatus.DISCARDED
            elif source in schematics:
                present = not schematics[source].discarded
            elif source in known_media:
                present = source in media
            else:
                raise NotFoundError
            files.append(FileMembership(source_id=source, present=present))
        primary = next((row.source_id for row in schematics.values() if row.is_primary), None)
        return AttachmentValues(files=tuple(files), primary=primary, select_primary=True)

    async def validate(self, draft: StoredDraft, values: AttachmentValues, limits: MediaLimits) -> AttachmentValues:
        session = self._session()
        supplied = {item.id: item for item in await PostgresAttachmentIntake(self._session).list(draft.snapshot.id)}
        membership = {item.source_id: item.present for item in values.files}
        active = {item.id for item in supplied.values() if item.status is not IntakeStatus.DISCARDED}
        active.difference_update(source for source, present in membership.items() if not present)
        active.update(source for source, present in membership.items() if present)
        if len(active) > 10:
            msg = "A submission can retain at most ten supplied files."
            raise ValidationError(msg)
        links = tuple(
            await session.scalars(select(DraftSchematicLink).where(DraftSchematicLink.draft_id == draft.snapshot.id))
        )
        retained = {link.source_id for link in links if membership.get(link.source_id, not link.discarded)}
        for source in sorted(retained | membership.keys()):
            blob = await session.scalar(
                select(DraftSchematicSource).where(DraftSchematicSource.id == source).with_for_update()
            )
            if (
                blob is not None
                and source in retained
                and blob.state in {DraftSchematicState.DELETING, DraftSchematicState.DELETED}
            ):
                msg = "The retained schematic has expired. Upload it again."
                raise ConflictError(msg)
        uploads = tuple(
            await session.scalars(
                select(MediaUploadRecord)
                .where(media_for_draft(draft.snapshot.id, include_discarded=True))
                .order_by(MediaUploadRecord.id)
            )
        )
        current_media = set(
            await session.scalars(select(MediaUploadRecord.id).where(media_for_draft(draft.snapshot.id)))
        )
        selected = []
        for upload in uploads:
            job = await session.scalar(
                select(MediaNormalizationJobRecord)
                .where(MediaNormalizationJobRecord.upload_id == upload.id)
                .with_for_update()
            )
            if membership.get(upload.id, upload.id in current_media):
                if job is None or job.status is MediaJobStatus.DISCARDED:
                    msg = "The retained media has expired. Upload it again."
                    raise ConflictError(msg)
                selected.append(upload)
        outputs = await session.scalar(
            select(func.coalesce(func.sum(MediaArtifactRecord.byte_size), 0)).where(
                MediaArtifactRecord.upload_id.in_([upload.id for upload in selected]),
                MediaArtifactRecord.role != MediaArtifactRole.REPORT,
            )
        )
        totals = MediaBatchTotals(
            image_count=sum(upload.kind is MediaKind.IMAGE for upload in selected),
            video_count=sum(upload.kind is MediaKind.VIDEO for upload in selected),
            source_bytes=sum(upload.source_byte_size for upload in selected),
            output_bytes=int(outputs or 0),
        )
        if violations := limits.batch_violations(totals):
            raise MediaLimitExceededError(violations)
        primary = values.primary
        if primary not in retained:
            if primary is not None and membership.get(primary) is not False:
                msg = "The primary schematic must be attached to this draft."
                raise ValidationError(msg)
            primary = next(iter(retained)) if len(retained) == 1 else None
        return values.model_copy(update={"primary": primary})

    async def write(self, draft: StoredDraft, values: AttachmentValues) -> int:
        session = self._session()
        model = await _editable(session, draft)
        supplied = {item.id: item for item in await PostgresAttachmentIntake(self._session).list(draft.snapshot.id)}
        # Clear the unique primary slot before restoring another reference.
        await session.execute(
            update(DraftSchematicLink).where(DraftSchematicLink.draft_id == draft.snapshot.id).values(is_primary=False)
        )
        for item in values.files:
            intake = await session.get(SubmissionSuppliedAttachment, (draft.snapshot.id, item.source_id))
            if intake is None and item.source_id in supplied:
                source = supplied[item.source_id]
                intake = SubmissionSuppliedAttachment(
                    draft_id=draft.snapshot.id,
                    source_id=source.id,
                    filename=source.filename,
                    kind=source.kind,
                    status=source.status,
                )
                session.add(intake)
            if intake is not None:
                intake.discarded = not item.present
            link = await session.get(DraftSchematicLink, (draft.snapshot.id, item.source_id))
            if link is not None:
                link.discarded = not item.present
            upload = await session.scalar(
                select(MediaUploadRecord).where(
                    MediaUploadRecord.id == item.source_id, media_for_draft(draft.snapshot.id, include_discarded=True)
                )
            )
            if upload is not None:
                reference = await session.get(MediaDraftReference, (draft.snapshot.id, item.source_id))
                if reference is None:
                    reference = MediaDraftReference(draft_id=draft.snapshot.id, upload_id=item.source_id)
                    session.add(reference)
                reference.discarded = not item.present
        await session.flush()
        if values.primary is not None:
            primary = await session.get(DraftSchematicLink, (draft.snapshot.id, values.primary))
            assert primary is not None
            primary.is_primary = True
        _changed(model)
        await session.flush()
        return model.revision
