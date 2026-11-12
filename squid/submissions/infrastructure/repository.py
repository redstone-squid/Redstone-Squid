"""PostgreSQL persistence for synchronized submission drafts."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import cast, override
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.errors import JSONValue
from squid.edits.domain import OperationReuseError
from squid.media.domain.jobs import MediaJobStatus
from squid.media.infrastructure.references import media_for_draft, retained_elsewhere
from squid.media.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.messages.domain import MessageSnapshot
from squid.persistence.advisory_locks import (
    AdvisoryLockNamespace,
    lock_key,
    lock_uuid,
)
from squid.submissions.application import AppliedDraftChange, AppliedDraftUpgrade, DraftRepository, StoredDraft
from squid.submissions.domain import (
    DraftChange,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
)
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftCapacityExceededError,
    DraftNotFoundError,
    DraftStateConflictError,
)
from squid.submissions.infrastructure.issues import decode_issues, encode_issues
from squid.submissions.models.entities import (
    SubmissionDraft,
    SubmissionDraftAccess,
    SubmissionDraftChange,
)
from squid.submissions.models.finalization import SubmissionFinalizationJob

_ACTIVE_STATUSES = (DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION)


class PostgresDraftRepository(DraftRepository):
    """Serialize draft edits and retain accepted changes for replay detection.

    Methods use the caller's transaction and do not commit. User-created drafts
    have per-account capacity; inferred drafts share one global capacity limit.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def list_attention(self, *, after: UUID | None, limit: int, now: Instant) -> tuple[StoredDraft, ...]:
        statement = (
            select(SubmissionDraft)
            .where(
                SubmissionDraft.status == DraftStatus.NEEDS_ATTENTION,
                SubmissionDraft.preparation_retry_at.is_(None),
                SubmissionDraft.expires_at > now,
            )
            .order_by(SubmissionDraft.id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(SubmissionDraft.id > after)
        session = self._session()
        return tuple(_to_stored(model) for model in await session.scalars(statement))

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
        session = self._session()
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
    async def create(self, draft: StoredDraft, *, capacity: int = 10) -> StoredDraft:
        """Serialize quota checks and creation; stable source IDs replay before consuming capacity."""
        if capacity < 1:
            message = "Draft capacity must be positive."
            raise ValueError(message)
        model = _to_model(draft)
        session = self._session()
        await lock_key(
            session,
            "inferred" if draft.inferred else f"account:{draft.snapshot.owner_account_id}",
            namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_CAPACITY,
        )
        existing = await session.get(SubmissionDraft, model.id)
        if existing is not None:
            if existing.owner_account_id != model.owner_account_id:
                raise DraftAccessDeniedError
            if (
                existing.schema_id,
                existing.schema_revision,
                existing.origin,
                existing.inferred,
                existing.category,
                existing.source_installation_id,
                existing.source_messages,
                existing.source_files,
                existing.inference_run_id,
            ) != (
                model.schema_id,
                model.schema_revision,
                model.origin,
                model.inferred,
                model.category,
                model.source_installation_id,
                model.source_messages,
                model.source_files,
                model.inference_run_id,
            ):
                raise DraftStateConflictError(existing.status.value, operation="create")
            return _to_stored(existing)
        count_query = (
            select(func.count())
            .select_from(SubmissionDraft)
            .where(
                SubmissionDraft.status.in_(_ACTIVE_STATUSES),
                SubmissionDraft.expires_at > func.now(),
                SubmissionDraft.inferred.is_(draft.inferred),
            )
        )
        if not draft.inferred:
            count_query = count_query.where(SubmissionDraft.owner_account_id == draft.snapshot.owner_account_id)
        if (await session.scalar(count_query) or 0) >= capacity:
            raise DraftCapacityExceededError(capacity)
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
        session = self._session()
        model = await session.get(SubmissionDraft, draft_id)
        return _to_stored(model) if model is not None else None

    @override
    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange | None:
        session = self._session()
        row = (
            await session.execute(
                select(SubmissionDraft, SubmissionDraftChange)
                .join(SubmissionDraftChange, SubmissionDraftChange.draft_id == SubmissionDraft.id)
                .where(
                    SubmissionDraft.id == draft_id,
                    SubmissionDraft.owner_account_id == account_id,
                    SubmissionDraftChange.idempotency_key == change.idempotency_key,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        _require_same_change(row[1], change, account_id if actor_account_id is None else actor_account_id)
        # Replay returns today's snapshot, not the snapshot when the edit was accepted.
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
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange:
        session = self._session()
        model = await self._locked(session, draft_id)
        self._require_owner(model, account_id)
        replay = await session.scalar(
            select(SubmissionDraftChange).where(
                SubmissionDraftChange.draft_id == draft_id,
                SubmissionDraftChange.idempotency_key == change.idempotency_key,
            )
        )
        if replay is not None:
            _require_same_change(replay, change, account_id if actor_account_id is None else actor_account_id)
            return AppliedDraftChange(_to_stored(model), replayed=True)

        candidate = _to_stored(model).snapshot.apply(change)
        model.revision = candidate.revision
        model.status = candidate.status
        model.preparation_issues = []
        model.preparation_retry_at = None
        model.source_issues = [
            issue
            for issue in model.source_issues
            if issue not in {operation.field_id for operation in change.operations}
        ]
        model.answers = _json_object(candidate.answers)
        model.updated_at = updated_at
        model.expires_at = expires_at
        session.add(
            SubmissionDraftChange(
                draft_id=draft_id,
                actor_account_id=account_id if actor_account_id is None else actor_account_id,
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
    async def upgrade_manifest(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        target_schema_revision: int,
        answers: Mapping[str, JSONValue],
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftUpgrade:
        session = self._session()
        model = await self._locked(session, draft_id)
        self._require_owner(model, account_id)
        if model.schema_revision == target_schema_revision:
            return AppliedDraftUpgrade(_to_stored(model), replayed=True)
        if model.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            raise DraftStateConflictError(model.status.value, operation="upgrade_manifest")
        if model.revision != expected_revision:
            raise DraftRevisionConflictError(expected=expected_revision, actual=model.revision)
        if target_schema_revision != model.schema_revision + 1:
            msg = "draft manifests must be upgraded by one revision"
            raise ValueError(msg)
        model.schema_revision = target_schema_revision
        model.revision += 1
        model.preparation_issues = []
        model.preparation_retry_at = None
        model.answers = _json_object(answers)
        model.updated_at = updated_at
        model.expires_at = expires_at
        return AppliedDraftUpgrade(_to_stored(model))

    @override
    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool:
        """Delete an editable owned draft behind the shared lifecycle fence."""
        session = self._session()
        await lock_uuid(session, draft_id, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
        model = await session.scalar(select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update())
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
                    select(MediaUploadRecord.id).where(media_for_draft(draft_id), ~retained_elsewhere(draft_id))
                ),
                MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED.value,
            )
            .values(
                status=MediaJobStatus.DISCARDED,
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

    @override
    async def expire_due(self, *, now: Instant, limit: int = 100) -> int:
        """Expire due drafts, remove unfinished finalization jobs, and discard unshared media jobs."""
        if not 1 <= limit <= 1_000:
            msg = "draft expiry limit must be between 1 and 1000"
            raise ValueError(msg)
        session = self._session()
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
            await lock_uuid(session, draft_id, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
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
                .values(status=DraftStatus.EXPIRED, updated_at=now, preparation_retry_at=None)
            )
            await session.execute(
                update(MediaNormalizationJobRecord)
                .where(
                    MediaNormalizationJobRecord.upload_id.in_(
                        select(MediaUploadRecord.id).where(media_for_draft(draft_id), ~retained_elsewhere(draft_id))
                    ),
                    MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED.value,
                )
                .values(
                    status=MediaJobStatus.DISCARDED,
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
        preparation_issues=encode_issues(draft.preparation_issues),
        preparation_retry_at=draft.preparation_retry_at,
        inferred=draft.inferred,
        submission_actor_account_id=draft.submission_actor_account_id,
        origin=draft.origin,
        source_installation_id=draft.source_installation_id,
        source_messages=TypeAdapter(tuple[MessageSnapshot, ...]).dump_python(draft.source_messages, mode="json"),
        source_files=TypeAdapter(tuple[SubmissionSourceFile, ...]).dump_python(draft.source_files, mode="json"),
        source_issues=list(draft.source_issues),
        inference_run_id=draft.inference_run_id,
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
        source_messages=TypeAdapter(tuple[MessageSnapshot, ...]).validate_python(model.source_messages),
        source_files=TypeAdapter(tuple[SubmissionSourceFile, ...]).validate_python(model.source_files),
        source_issues=tuple(model.source_issues),
        inference_run_id=model.inference_run_id,
        preparation_issues=decode_issues(model.preparation_issues),
        preparation_retry_at=model.preparation_retry_at,
        inferred=model.inferred,
        submission_actor_account_id=model.submission_actor_account_id,
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


def _require_same_change(row: SubmissionDraftChange, change: DraftChange, actor_account_id: int) -> None:
    if (
        row.actor_account_id != actor_account_id
        or row.base_revision != change.base_revision
        or row.client_instance_id != change.client_instance_id
        or json.dumps(row.operations, sort_keys=True)
        != json.dumps(_operations_to_json(change.operations), sort_keys=True)
    ):
        raise OperationReuseError
