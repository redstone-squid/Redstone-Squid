"""Reference-safe persistence for private schematic intake."""

from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.selectable import Exists
from whenever import Instant

from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.domain import DraftStatus
from squid.submissions.domain.finalization import (
    SchematicArtifactState,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.submissions.errors import DraftAccessDeniedError, DraftStateConflictError
from squid.submissions.infrastructure.artifact_readiness import DraftSchematicSnapshot
from squid.submissions.infrastructure.finalization_repository import _locked_draft, _require_expected_draft
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.schematic_models import DraftSchematicLink, DraftSchematicSource

MAX_DRAFT_SCHEMATICS = 10


class PostgresDraftSchematics:
    """Retain source identity, independent references, and cleanup progress."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def register(self, draft: StoredDraft, source: DraftSchematic, actor_id: int) -> DraftSchematic:
        async with self._sessions.begin() as session:
            model = await _editable(session, draft)
            await lock_uuid(session, source.id, namespace=AdvisoryLockNamespace.SUBMISSION_SCHEMATIC_SOURCE)
            existing = await session.scalar(
                select(DraftSchematicSource).where(DraftSchematicSource.id == source.id).with_for_update()
            )
            if existing is not None:
                if existing.owner_account_id != draft.snapshot.owner_account_id:
                    raise DraftAccessDeniedError
                if existing.filename != source.filename or (
                    existing.sha256 is not None
                    and source.sha256 is not None
                    and (existing.sha256, existing.byte_size) != (source.sha256, source.byte_size)
                ):
                    msg = "This upload ID already identifies different schematic bytes."
                    raise ConflictError(msg)
                if existing.sha256 is None and source.sha256 is not None:
                    existing.sha256 = source.sha256
                    existing.byte_size = source.byte_size
                if existing.state in {DraftSchematicState.DELETING, DraftSchematicState.DELETED}:
                    msg = "This schematic source has expired; upload it with a new identifier."
                    raise ConflictError(msg)
                if existing.state is not DraftSchematicState.WAITING_SANITIZER:
                    existing.state = DraftSchematicState.UPLOADING
                    existing.updated_at = Instant.now()
            else:
                existing = DraftSchematicSource(
                    id=source.id,
                    owner_account_id=draft.snapshot.owner_account_id,
                    uploaded_by_account_id=actor_id,
                    filename=source.filename,
                    sha256=source.sha256,
                    byte_size=source.byte_size,
                    state=DraftSchematicState.UPLOADING,
                )
                session.add(existing)
                await session.flush()
            link = await _link(session, model, source.id)
            return _snapshot(existing, link)

    async def finish_upload(self, upload_id: UUID, *, succeeded: bool) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(DraftSchematicSource)
                .where(
                    DraftSchematicSource.id == upload_id,
                    DraftSchematicSource.state.in_((DraftSchematicState.UPLOADING, DraftSchematicState.FAILED))
                    if succeeded
                    else DraftSchematicSource.state == DraftSchematicState.UPLOADING,
                )
                .values(
                    state=DraftSchematicState.WAITING_SANITIZER if succeeded else DraftSchematicState.FAILED,
                    updated_at=func.now(),
                )
            )

    async def list(self, draft_id: UUID) -> tuple[DraftSchematic, ...]:
        async with self._sessions() as session:
            rows = await session.execute(
                select(DraftSchematicSource, DraftSchematicLink)
                .join(
                    DraftSchematicLink,
                    DraftSchematicLink.source_id == DraftSchematicSource.id,
                )
                .where(DraftSchematicLink.draft_id == draft_id)
                .order_by(DraftSchematicSource.created_at, DraftSchematicSource.id)
            )
            return tuple(_snapshot(source, link) for source, link in rows)

    async def select_primary(self, draft: StoredDraft, upload_id: UUID) -> None:
        async with self._sessions.begin() as session:
            model = await _editable(session, draft)
            link = await session.get(DraftSchematicLink, (draft.snapshot.id, upload_id))
            if link is None or link.discarded:
                msg = "That schematic is not attached to this draft."
                raise NotFoundError(msg)
            if link.is_primary:
                return
            await session.execute(
                update(DraftSchematicLink)
                .where(DraftSchematicLink.draft_id == draft.snapshot.id)
                .values(is_primary=False)
            )
            link.is_primary = True
            _changed(model)

    async def discard(self, draft: StoredDraft, upload_id: UUID) -> None:
        async with self._sessions.begin() as session:
            model = await _editable(session, draft)
            link = await session.get(DraftSchematicLink, (draft.snapshot.id, upload_id))
            if link is None:
                msg = "That schematic is not attached to this draft."
                raise NotFoundError(msg)
            if link.discarded:
                return
            link.is_primary = False
            link.discarded = True
            await session.flush()
            remaining = tuple(
                await session.scalars(
                    select(DraftSchematicLink).where(
                        DraftSchematicLink.draft_id == draft.snapshot.id,
                        DraftSchematicLink.discarded.is_(False),
                    )
                )
            )
            if len(remaining) == 1:
                remaining[0].is_primary = True
            _changed(model)

    async def attach(self, source: StoredDraft, destination: StoredDraft, upload_id: UUID) -> None:
        async with self._sessions.begin() as session:
            locked = {
                draft_id: await _locked_draft(session, draft_id)
                for draft_id in sorted({source.snapshot.id, destination.snapshot.id})
            }
            _require_expected_draft(locked[source.snapshot.id], source)
            target = locked[destination.snapshot.id]
            _require_editable(target, destination)
            if source.snapshot.owner_account_id != destination.snapshot.owner_account_id:
                raise DraftAccessDeniedError
            link = await session.get(DraftSchematicLink, (source.snapshot.id, upload_id))
            blob = await session.scalar(
                select(DraftSchematicSource).where(DraftSchematicSource.id == upload_id).with_for_update()
            )
            if (
                link is None
                or link.discarded
                or blob is None
                or blob.state in {DraftSchematicState.DELETING, DraftSchematicState.DELETED}
            ):
                msg = "That source schematic is unavailable."
                raise NotFoundError(msg)
            await _link(session, target, upload_id)

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot:
        retained = [item for item in await self.list(draft_id) if not item.discarded]
        if not retained:
            return DraftSchematicSnapshot(SchematicArtifactState.ABSENT)
        if any(
            item.state in {DraftSchematicState.FAILED, DraftSchematicState.DELETING, DraftSchematicState.DELETED}
            for item in retained
        ):
            return DraftSchematicSnapshot(SchematicArtifactState.REJECTED)
        issues = (
            ()
            if len(retained) == 1 or any(item.primary for item in retained)
            else (SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PRIMARY_REQUIRED),)
        )
        # Schem-at/Nucleation#39: quarantine is retained, never treated as sanitized output.
        return DraftSchematicSnapshot(SchematicArtifactState.PROCESSING, issues=issues)

    async def cleanup_candidates(self, *, limit: int) -> tuple[UUID, ...]:
        if not 1 <= limit <= 100:
            msg = "Invalid schematic cleanup batch limit."
            raise ValueError(msg)
        async with self._sessions.begin() as session:
            candidates = tuple(
                await session.scalars(
                    select(DraftSchematicSource.id)
                    .where(
                        DraftSchematicSource.state != DraftSchematicState.DELETED,
                        DraftSchematicSource.updated_at < Instant.now().subtract(minutes=5),
                        ~_has_retained_reference(),
                    )
                    .order_by(DraftSchematicSource.updated_at, DraftSchematicSource.id)
                    .limit(limit)
                )
            )
            claimed: list[UUID] = []
            draft_ids = tuple(
                await session.scalars(
                    select(DraftSchematicLink.draft_id)
                    .where(
                        DraftSchematicLink.source_id.in_(candidates),
                    )
                    .distinct()
                    .order_by(DraftSchematicLink.draft_id)
                )
            )
            for draft_id in draft_ids:
                await session.scalar(select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update())
            for upload_id in sorted(candidates):
                source = await session.scalar(
                    select(DraftSchematicSource)
                    .where(
                        DraftSchematicSource.id == upload_id,
                        ~_has_retained_reference(),
                        DraftSchematicSource.updated_at < Instant.now().subtract(minutes=5),
                    )
                    .with_for_update()
                )
                if source is None:
                    continue
                # Re-read after the row lock: another candidate may have linked the source
                # while this transaction waited for an uploader holding that lock.
                retained = await session.scalar(
                    select(DraftSchematicSource.id).where(
                        DraftSchematicSource.id == upload_id,
                        _has_retained_reference(),
                    )
                )
                if retained is not None or source.state is DraftSchematicState.DELETED:
                    continue
                source.state = DraftSchematicState.DELETING
                claimed.append(source.id)
            return tuple(claimed)

    async def mark_deleted(self, upload_id: UUID) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(DraftSchematicSource)
                .where(
                    DraftSchematicSource.id == upload_id,
                    DraftSchematicSource.state == DraftSchematicState.DELETING,
                )
                .values(state=DraftSchematicState.DELETED, updated_at=func.now())
            )


def _has_retained_reference() -> Exists:
    return exists(
        select(DraftSchematicLink.source_id)
        .join(SubmissionDraft, SubmissionDraft.id == DraftSchematicLink.draft_id)
        .where(
            DraftSchematicLink.source_id == DraftSchematicSource.id,
            DraftSchematicLink.discarded.is_(False),
            (SubmissionDraft.status == DraftStatus.SUBMITTED)
            | ((SubmissionDraft.status != DraftStatus.EXPIRED) & (SubmissionDraft.expires_at > func.now())),
        )
    )


async def _editable(session: AsyncSession, draft: StoredDraft) -> SubmissionDraft:
    model = await _locked_draft(session, draft.snapshot.id)
    _require_editable(model, draft)
    return model


def _require_editable(model: SubmissionDraft, draft: StoredDraft) -> None:
    _require_expected_draft(model, draft)
    if model.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION} or model.expires_at <= Instant.now():
        raise DraftStateConflictError(model.status.value, operation="attachments")


async def _link(session: AsyncSession, draft: SubmissionDraft, upload_id: UUID) -> DraftSchematicLink:
    existing = await session.get(DraftSchematicLink, (draft.id, upload_id))
    if existing is not None and not existing.discarded:
        return existing
    count = (
        await session.scalar(
            select(func.count())
            .select_from(DraftSchematicLink)
            .where(
                DraftSchematicLink.draft_id == draft.id,
                DraftSchematicLink.discarded.is_(False),
            )
        )
        or 0
    )
    if count >= MAX_DRAFT_SCHEMATICS:
        msg = "A draft cannot retain more than ten schematics."
        raise ValidationError(msg)
    if count:
        await session.execute(
            update(DraftSchematicLink).where(DraftSchematicLink.draft_id == draft.id).values(is_primary=False)
        )
    if existing is None:
        existing = DraftSchematicLink(draft_id=draft.id, source_id=upload_id, is_primary=count == 0)
        session.add(existing)
    else:
        existing.discarded = False
        existing.is_primary = count == 0
    _changed(draft)
    return existing


def _changed(draft: SubmissionDraft) -> None:
    draft.revision += 1
    draft.status = DraftStatus.EDITING
    draft.preparation_issues = []
    draft.preparation_retry_at = None
    draft.updated_at = Instant.now()


def _snapshot(source: DraftSchematicSource, link: DraftSchematicLink) -> DraftSchematic:
    return DraftSchematic(
        source.id, source.filename, source.sha256, source.byte_size, source.state, link.is_primary, link.discarded
    )
