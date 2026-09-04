"""PostgreSQL persistence for synchronized submission drafts."""

from collections.abc import Mapping, Sequence
from typing import cast, override
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import JSONValue
from squid.media.application.jobs import MediaJobStatus
from squid.media.infrastructure.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.advisory_locks import SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE, lock_uuid
from squid.submissions.application import AppliedDraftChange, DraftRepository, StoredDraft
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
)
from squid.submissions.errors import DraftAccessDeniedError, DraftNotFoundError, DraftStateConflictError
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.models import (
    SubmissionDraft,
    SubmissionDraftAccess,
    SubmissionDraftChange,
)

_ACTIVE_STATUSES = (DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION)


class PostgresDraftRepository(DraftRepository):
    """Serialize mutations per draft and retain immutable accepted changes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def count_active_for_account(self, account_id: int) -> int:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SubmissionDraft)
                .where(
                    SubmissionDraft.owner_account_id == account_id,
                    SubmissionDraft.status.in_(_ACTIVE_STATUSES),
                    SubmissionDraft.expires_at > func.now(),
                )
            )
        return int(count or 0)

    @override
    async def list_active_for_account(
        self,
        account_id: int,
        *,
        now: Instant,
        limit: int,
    ) -> tuple[StoredDraft, ...]:
        if not 1 <= limit <= 10:
            msg = "active draft list limit must be between 1 and 10"
            raise ValueError(msg)
        async with self._session_factory() as session:
            models = tuple(
                (
                    await session.scalars(
                        select(SubmissionDraft)
                        .where(
                            SubmissionDraft.owner_account_id == account_id,
                            SubmissionDraft.status.in_(_ACTIVE_STATUSES),
                            SubmissionDraft.expires_at > now,
                        )
                        .order_by(SubmissionDraft.updated_at.desc(), SubmissionDraft.id)
                        .limit(limit)
                    )
                ).all()
            )
        return tuple(_to_stored(model) for model in models)

    @override
    async def create(self, draft: StoredDraft) -> StoredDraft:
        model = _to_model(draft)
        async with self._session_factory.begin() as session:
            session.add(model)
            session.add(
                SubmissionDraftAccess(
                    draft_id=model.id,
                    account_id=model.owner_account_id,
                    role="owner",
                )
            )
        return _to_stored(model)

    @override
    async def get(self, draft_id: UUID) -> StoredDraft | None:
        async with self._session_factory() as session:
            model = await session.get(SubmissionDraft, draft_id)
            return _to_stored(model) if model is not None else None

    @override
    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: DraftChangeKey,
    ) -> AppliedDraftChange | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SubmissionDraft, SubmissionDraftChange.id)
                    .join(SubmissionDraftChange, SubmissionDraftChange.draft_id == SubmissionDraft.id)
                    .where(
                        SubmissionDraft.id == draft_id,
                        SubmissionDraft.owner_account_id == account_id,
                        SubmissionDraftChange.idempotency_key == idempotency_key,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return AppliedDraftChange(_to_stored(row[0]), replayed=True)

    @override
    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftChange:
        async with self._session_factory.begin() as session:
            model = await self._locked(session, draft_id)
            self._require_owner(model, account_id)
            replay = await session.scalar(
                select(SubmissionDraftChange.id).where(
                    SubmissionDraftChange.draft_id == draft_id,
                    SubmissionDraftChange.idempotency_key == change.idempotency_key,
                )
            )
            if replay is not None:
                return AppliedDraftChange(_to_stored(model), replayed=True)

            candidate = _to_stored(model).snapshot.apply(change)
            model.revision = candidate.revision
            model.status = candidate.status
            model.answers = _json_object(candidate.answers)
            model.updated_at = updated_at
            model.expires_at = expires_at
            session.add(
                SubmissionDraftChange(
                    draft_id=draft_id,
                    actor_account_id=account_id,
                    base_revision=change.base_revision,
                    resulting_revision=candidate.revision,
                    client_instance_id=change.client_instance_id,
                    idempotency_key=str(change.idempotency_key),
                    operations=_operations_to_json(change.operations),
                    applied_at=updated_at,
                )
            )
        return AppliedDraftChange(_to_stored(model))

    @override
    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        updated_at: Instant,
        expires_at: Instant,
    ) -> StoredDraft:
        async with self._session_factory.begin() as session:
            model = await self._locked(session, draft_id)
            self._require_owner(model, account_id)
            snapshot = _to_stored(model).snapshot
            if snapshot.revision != expected_revision:
                raise DraftRevisionConflictError(expected=expected_revision, actual=snapshot.revision)
            candidate = snapshot.transition(status)
            model.status = candidate.status
            model.updated_at = updated_at
            model.expires_at = expires_at
        return _to_stored(model)

    @override
    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool:
        """Delete an editable owned draft behind the shared lifecycle fence."""
        async with self._session_factory.begin() as session:
            await lock_uuid(session, draft_id, namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE)
            model = await session.scalar(
                select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update()
            )
            if model is None:
                return False
            self._require_owner(model, account_id)
            status = model.status
            if status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                raise DraftStateConflictError(status.value, operation="delete")
            # Retain artifact rows and object keys: normalized content is addressable by hash and may be shared,
            # so deleting it safely requires reference-aware garbage collection rather than a draft cascade.
            await session.execute(
                update(MediaNormalizationJobRecord)
                .where(
                    MediaNormalizationJobRecord.upload_id.in_(
                        select(MediaUploadRecord.id).where(MediaUploadRecord.draft_id == draft_id)
                    ),
                    MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED.value,
                )
                .values(
                    status=MediaJobStatus.DISCARDED.value,
                    claimed_at=None,
                    claim_token=None,
                    completed_at=None,
                    dead_at=None,
                    discarded_at=func.now(),
                    last_error=None,
                )
            )
            await session.delete(model)
        return True

    async def expire_due(self, *, now: Instant, limit: int = 100) -> int:
        """Expire a fenced batch and cancel every unfinished artifact workflow."""
        if not 1 <= limit <= 1_000:
            msg = "draft expiry limit must be between 1 and 1000"
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            due = tuple(
                (
                    await session.scalars(
                        select(SubmissionDraft.id)
                        .where(
                            SubmissionDraft.status.in_(_ACTIVE_STATUSES),
                            SubmissionDraft.expires_at <= now,
                        )
                        .order_by(SubmissionDraft.expires_at, SubmissionDraft.id)
                        .limit(limit)
                    )
                ).all()
            )
            expired = 0
            for draft_id in due:
                await lock_uuid(session, draft_id, namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE)
                model = await session.scalar(
                    select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update()
                )
                if model is None or model.status not in _ACTIVE_STATUSES or model.expires_at > now:
                    continue
                await session.execute(
                    delete(SubmissionFinalizationJob).where(
                        SubmissionFinalizationJob.draft_id == draft_id,
                        SubmissionFinalizationJob.status != "completed",
                    )
                )
                await session.execute(
                    update(SubmissionDraft)
                    .where(SubmissionDraft.id == draft_id)
                    .values(status=DraftStatus.EXPIRED, updated_at=now)
                )
                await session.execute(
                    update(MediaNormalizationJobRecord)
                    .where(
                        MediaNormalizationJobRecord.upload_id.in_(
                            select(MediaUploadRecord.id).where(MediaUploadRecord.draft_id == draft_id)
                        ),
                        MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED.value,
                    )
                    .values(
                        status=MediaJobStatus.DISCARDED.value,
                        claimed_at=None,
                        claim_token=None,
                        completed_at=None,
                        dead_at=None,
                        discarded_at=now,
                        last_error=None,
                    )
                )
                expired += 1
        return expired

    @staticmethod
    async def _locked(session: AsyncSession, draft_id: UUID) -> SubmissionDraft:
        model = await session.scalar(select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update())
        if model is None:
            raise DraftNotFoundError(draft_id)
        return model

    @staticmethod
    def _require_owner(model: SubmissionDraft, account_id: int) -> None:
        if model.owner_account_id != account_id:
            raise DraftAccessDeniedError


def _to_model(draft: StoredDraft) -> SubmissionDraft:
    snapshot = draft.snapshot
    return SubmissionDraft(
        id=snapshot.id,
        owner_account_id=snapshot.owner_account_id,
        schema_id=snapshot.schema_id,
        schema_revision=snapshot.schema_revision,
        category=snapshot.category,
        revision=snapshot.revision,
        status=snapshot.status,
        answers=_json_object(snapshot.answers),
        origin=draft.origin,
        source_installation_id=draft.source_installation_id,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        expires_at=draft.expires_at,
    )


def _to_stored(model: SubmissionDraft) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=model.id,
            owner_account_id=model.owner_account_id,
            schema_id=model.schema_id,
            schema_revision=model.schema_revision,
            category=model.category,
            revision=model.revision,
            status=model.status,
            answers=cast(Mapping[str, JSONValue], model.answers),
        ),
        origin=model.origin,
        created_at=model.created_at,
        updated_at=model.updated_at,
        expires_at=model.expires_at,
        source_installation_id=model.source_installation_id,
    )


def _json_object(value: Mapping[str, JSONValue]) -> dict[str, object]:
    return cast(dict[str, object], dict(value))


def _operations_to_json(operations: Sequence[FieldOperation]) -> list[dict[str, object]]:
    return [
        {
            "operation_id": str(operation.operation_id),
            "field_id": operation.field_id,
            "kind": operation.kind.value,
            **({"value": operation.value} if operation.kind is FieldOperationKind.SET else {}),
        }
        for operation in operations
    ]
