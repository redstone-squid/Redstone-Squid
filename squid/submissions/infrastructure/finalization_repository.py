"""PostgreSQL persistence for durable submission finalization."""

from collections.abc import Mapping, Sequence
from typing import override
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError, ValidationError
from squid.media.application.jobs import MediaJobStatus
from squid.media.infrastructure.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.advisory_locks import SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE, lock_uuid
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.finalization import (
    MAX_FINALIZATION_JOB_CLAIM,
    ClaimedFinalizationJob,
    FinalizationFailureOutcome,
    FinalizationJobRepository,
    FinalizationJobSnapshot,
)
from squid.submissions.domain import DraftRevisionConflictError, DraftStatus
from squid.submissions.domain.finalization import (
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.errors import DraftAccessDeniedError, DraftArtifactsChangedError, DraftNotFoundError
from squid.submissions.infrastructure.finalization_models import (
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
)
from squid.submissions.infrastructure.finalization_payloads import decode_submission, encode_submission
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.payload_integrity import submission_payload_digest

_CLAIM_MINUTES = 5


class PostgresFinalizationJobRepository(FinalizationJobRepository):
    """Atomically coordinate draft state and UUID-fenced finalization jobs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None:
        async with self._session_factory() as session:
            job = await session.scalar(
                select(SubmissionFinalizationJob).where(SubmissionFinalizationJob.draft_id == draft_id)
            )
            if job is None:
                return None
            result = await session.get(SubmissionFinalizationResult, job.id)
        return _snapshot(job, result)

    @override
    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot:
        """Transition a draft and upsert its immutable pending payload in one transaction."""
        if (
            payload.source_draft_id != draft.snapshot.id
            or payload.owner_account_id != draft.snapshot.owner_account_id
            or payload.origin is not draft.origin
            or payload.schema_id != draft.snapshot.schema_id
            or payload.schema_revision != draft.snapshot.schema_revision
            or payload.category.value != draft.snapshot.category
            or payload.source_installation_id != draft.source_installation_id
        ):
            msg = "normalized submission provenance does not match its source draft"
            raise ValueError(msg)
        encoded = encode_submission(payload)
        digest = submission_payload_digest(encoded)
        async with self._session_factory.begin() as session:
            draft_model = await _locked_draft(session, draft.snapshot.id)
            _require_expected_draft(draft_model, draft)
            await _require_current_media(session, draft.snapshot.id, payload.artifacts.normalized_media_upload_ids)
            job = await _locked_job(session, draft.snapshot.id)
            status = draft_model.status
            if status in {DraftStatus.PROCESSING, DraftStatus.SUBMITTED}:
                if job is None or job.payload_sha256 != digest:
                    msg = f"{status.value} draft has no matching finalization job"
                    raise InvalidStateError(msg)
                expected_job_statuses = (
                    {FinalizationJobStatus.PENDING, FinalizationJobStatus.CLAIMED}
                    if status is DraftStatus.PROCESSING
                    else {FinalizationJobStatus.COMPLETED}
                )
                if job.status not in expected_job_statuses:
                    msg = f"{status.value} draft has an incompatible {job.status} finalization job"
                    raise InvalidStateError(msg)
                result = await session.get(SubmissionFinalizationResult, job.id)
                return _snapshot(job, result)
            if status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                msg = f"drafts in {status.value} state cannot be finalized"
                raise ValidationError(msg, resource="submission_draft")

            draft_model.status = DraftStatus.PROCESSING
            draft_model.updated_at = now
            draft_model.expires_at = expires_at
            if job is None:
                job = SubmissionFinalizationJob(
                    draft_id=draft.snapshot.id,
                    draft_revision=draft.snapshot.revision,
                    payload=encoded,
                    payload_sha256=digest,
                    status=FinalizationJobStatus.PENDING,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
            else:
                _reset_pending(job, draft.snapshot.revision, encoded, digest, now)
        return _snapshot(job, None)

    @override
    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> FinalizationJobSnapshot:
        """Retain manifest/artifact issues and keep the source draft editable."""
        normalized_issues = _unique_issues(issues)
        if not normalized_issues:
            msg = "preparation attention requires at least one issue"
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            draft_model = await _locked_draft(session, draft.snapshot.id)
            _require_expected_draft(draft_model, draft)
            job = await _locked_job(session, draft.snapshot.id)
            status = draft_model.status
            if status in {DraftStatus.PROCESSING, DraftStatus.SUBMITTED}:
                if job is None:
                    msg = f"{status.value} draft has no finalization job"
                    raise InvalidStateError(msg)
                result = await session.get(SubmissionFinalizationResult, job.id)
                return _snapshot(job, result)
            if status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                msg = f"drafts in {status.value} state cannot request finalization"
                raise ValidationError(msg, resource="submission_draft")

            draft_model.status = DraftStatus.NEEDS_ATTENTION
            draft_model.updated_at = now
            draft_model.expires_at = expires_at
            if job is None:
                job = SubmissionFinalizationJob(
                    draft_id=draft.snapshot.id,
                    draft_revision=draft.snapshot.revision,
                    payload=None,
                    payload_sha256=None,
                    status=FinalizationJobStatus.NEEDS_ATTENTION,
                    available_at=now,
                    attention_at=now,
                    attention_issues=_encode_issues(normalized_issues),
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
            else:
                _reset_attention(job, draft.snapshot.revision, normalized_issues, now)
        return _snapshot(job, None)

    @override
    async def claim(self, *, now: Instant, limit: int) -> Sequence[ClaimedFinalizationJob]:
        """Lease ready or abandoned work with a fresh UUID fence."""
        if not 1 <= limit <= MAX_FINALIZATION_JOB_CLAIM:
            msg = f"finalization claim limit must be between 1 and {MAX_FINALIZATION_JOB_CLAIM}"
            raise ValueError(msg)
        ready = or_(
            and_(
                SubmissionFinalizationJob.status == FinalizationJobStatus.PENDING,
                SubmissionFinalizationJob.available_at <= now,
            ),
            and_(
                SubmissionFinalizationJob.status == FinalizationJobStatus.CLAIMED,
                SubmissionFinalizationJob.claim_expires_at <= now,
            ),
        )
        async with self._session_factory() as session:
            jobs = tuple(
                (
                    await session.scalars(
                        select(SubmissionFinalizationJob)
                        .where(ready)
                        .order_by(SubmissionFinalizationJob.available_at, SubmissionFinalizationJob.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claims: list[ClaimedFinalizationJob] = []
            for job in jobs:
                payload = job.payload
                if not isinstance(payload, dict):
                    msg = "claimable finalization job has no JSON object payload"
                    raise DataIntegrityError(msg)
                if job.payload_sha256 != submission_payload_digest(payload):
                    msg = "claimable finalization job failed its payload integrity check"
                    raise DataIntegrityError(msg)
                token = uuid4()
                job.status = FinalizationJobStatus.CLAIMED
                job.attempts += 1
                job.claimed_at = now
                job.claim_token = token
                job.claim_expires_at = now.add(minutes=_CLAIM_MINUTES)
                job.updated_at = now
                claims.append(
                    ClaimedFinalizationJob(
                        job_id=job.id,
                        draft_id=job.draft_id,
                        draft_revision=job.draft_revision,
                        payload=decode_submission(payload),
                        attempts=job.attempts,
                        claimed_at=now,
                        claim_token=token,
                    )
                )
            await session.commit()
        return tuple(claims)

    @override
    async def complete(
        self,
        job: ClaimedFinalizationJob,
        result: FinalizedBuild,
        *,
        now: Instant,
    ) -> bool:
        """Retain the target result and submit the draft if this claim still owns it."""
        async with self._session_factory.begin() as session:
            draft = await _locked_draft(session, job.draft_id)
            model = await _claimed_job(session, job)
            if model is None:
                return False
            if draft.status is not DraftStatus.PROCESSING:
                msg = "claimed finalization job does not own a processing draft"
                raise InvalidStateError(msg)
            existing = await session.get(SubmissionFinalizationResult, model.id)
            if existing is not None:
                if _result(existing) != result:
                    msg = "submission target returned conflicting results for one source draft"
                    raise DataIntegrityError(msg)
            else:
                session.add(
                    SubmissionFinalizationResult(
                        job_id=model.id,
                        build_id=result.build_id,
                        created_at=now,
                    )
                )
            model.status = FinalizationJobStatus.COMPLETED
            model.completed_at = now
            model.attention_at = None
            model.dead_at = None
            model.last_error = None
            model.attention_issues = []
            _clear_claim(model)
            model.updated_at = now
            draft.status = DraftStatus.SUBMITTED
            draft.updated_at = now
        return True

    @override
    async def needs_attention(
        self,
        job: ClaimedFinalizationJob,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> bool:
        """Release actionable target failures back to an editable draft."""
        normalized_issues = _unique_issues(issues)
        if not normalized_issues:
            msg = "target attention requires at least one issue"
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            draft = await _locked_draft(session, job.draft_id)
            model = await _claimed_job(session, job)
            if model is None:
                return False
            if draft.status is not DraftStatus.PROCESSING:
                msg = "claimed finalization job does not own a processing draft"
                raise InvalidStateError(msg)
            model.status = FinalizationJobStatus.NEEDS_ATTENTION
            model.attention_at = now
            model.dead_at = None
            model.completed_at = None
            model.last_error = "actionable"
            model.attention_issues = _encode_issues(normalized_issues)
            _clear_claim(model)
            model.updated_at = now
            draft.status = DraftStatus.NEEDS_ATTENTION
            draft.updated_at = now
            draft.expires_at = expires_at
        return True

    @override
    async def fail(
        self,
        job: ClaimedFinalizationJob,
        error: str,
        *,
        now: Instant,
        retry_at: Instant,
        expires_at: Instant,
        max_attempts: int,
    ) -> FinalizationFailureOutcome:
        """Retry unexpected failures, then retain a dead letter and editable draft."""
        if max_attempts < 1:
            msg = "finalization attempts must be positive"
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            draft = await _locked_draft(session, job.draft_id)
            model = await _claimed_job(session, job)
            if model is None:
                return FinalizationFailureOutcome(applied=False, dead=False)
            dead = model.attempts >= max_attempts
            model.last_error = error[:4000]
            model.completed_at = None
            _clear_claim(model)
            model.updated_at = now
            if dead:
                issue = SubmissionAttentionIssue("submission", SubmissionAttentionReason.RETRY_EXHAUSTED)
                model.status = FinalizationJobStatus.DEAD
                model.dead_at = now
                model.attention_at = None
                model.attention_issues = _encode_issues((issue,))
                draft.status = DraftStatus.NEEDS_ATTENTION
                draft.updated_at = now
                draft.expires_at = expires_at
            else:
                model.status = FinalizationJobStatus.PENDING
                model.available_at = retry_at
                model.dead_at = None
                model.attention_at = None
                model.attention_issues = []
        return FinalizationFailureOutcome(applied=True, dead=dead)


async def _locked_draft(session: AsyncSession, draft_id: UUID) -> SubmissionDraft:
    await lock_uuid(session, draft_id, namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE)
    model = await session.scalar(select(SubmissionDraft).where(SubmissionDraft.id == draft_id).with_for_update())
    if model is None:
        raise DraftNotFoundError(draft_id)
    return model


async def _require_current_media(
    session: AsyncSession,
    draft_id: UUID,
    expected_upload_ids: Sequence[UUID],
) -> None:
    rows = tuple(
        (
            await session.execute(
                select(MediaUploadRecord.id, MediaNormalizationJobRecord.status)
                .outerjoin(
                    MediaNormalizationJobRecord,
                    MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id,
                )
                .where(MediaUploadRecord.draft_id == draft_id)
                .with_for_update(of=MediaUploadRecord)
            )
        ).all()
    )
    retained = tuple((upload_id, status) for upload_id, status in rows if status != MediaJobStatus.DISCARDED.value)
    if any(status != MediaJobStatus.COMPLETED.value for _, status in retained) or {
        upload_id for upload_id, _ in retained
    } != set(expected_upload_ids):
        raise DraftArtifactsChangedError


async def _locked_job(session: AsyncSession, draft_id: UUID) -> SubmissionFinalizationJob | None:
    return await session.scalar(
        select(SubmissionFinalizationJob).where(SubmissionFinalizationJob.draft_id == draft_id).with_for_update()
    )


async def _claimed_job(
    session: AsyncSession,
    claim: ClaimedFinalizationJob,
) -> SubmissionFinalizationJob | None:
    return await session.scalar(
        select(SubmissionFinalizationJob)
        .where(
            SubmissionFinalizationJob.id == claim.job_id,
            SubmissionFinalizationJob.draft_id == claim.draft_id,
            SubmissionFinalizationJob.status == FinalizationJobStatus.CLAIMED,
            SubmissionFinalizationJob.claim_token == claim.claim_token,
        )
        .with_for_update()
    )


def _require_expected_draft(model: SubmissionDraft, expected: StoredDraft) -> None:
    if model.owner_account_id != expected.snapshot.owner_account_id:
        raise DraftAccessDeniedError
    if model.revision != expected.snapshot.revision:
        raise DraftRevisionConflictError(expected=expected.snapshot.revision, actual=model.revision)
    if model.origin is not expected.origin or model.source_installation_id != expected.source_installation_id:
        msg = "submission draft installation provenance changed during finalization"
        raise InvalidStateError(msg)


def _reset_pending(
    job: SubmissionFinalizationJob,
    revision: int,
    payload: dict[str, object],
    digest: str,
    now: Instant,
) -> None:
    job.draft_revision = revision
    job.payload = payload
    job.payload_sha256 = digest
    job.status = FinalizationJobStatus.PENDING
    job.attempts = 0
    job.available_at = now
    job.completed_at = None
    job.attention_at = None
    job.dead_at = None
    job.last_error = None
    job.attention_issues = []
    _clear_claim(job)
    job.updated_at = now


def _reset_attention(
    job: SubmissionFinalizationJob,
    revision: int,
    issues: tuple[SubmissionAttentionIssue, ...],
    now: Instant,
) -> None:
    job.draft_revision = revision
    job.payload = None
    job.payload_sha256 = None
    job.status = FinalizationJobStatus.NEEDS_ATTENTION
    job.attempts = 0
    job.completed_at = None
    job.attention_at = now
    job.dead_at = None
    job.last_error = "preparation"
    job.attention_issues = _encode_issues(issues)
    _clear_claim(job)
    job.updated_at = now


def _clear_claim(job: SubmissionFinalizationJob) -> None:
    job.claimed_at = None
    job.claim_token = None
    job.claim_expires_at = None


def _snapshot(
    job: SubmissionFinalizationJob,
    result: SubmissionFinalizationResult | None,
) -> FinalizationJobSnapshot:
    return FinalizationJobSnapshot(
        job_id=job.id,
        draft_id=job.draft_id,
        draft_revision=job.draft_revision,
        status=job.status,
        attempts=job.attempts,
        available_at=job.available_at,
        claimed_at=job.claimed_at,
        claim_token=job.claim_token,
        completed_at=job.completed_at,
        attention_at=job.attention_at,
        dead_at=job.dead_at,
        last_error=job.last_error,
        issues=_decode_issues(job.attention_issues),
        result=_result(result) if result is not None else None,
    )


def _result(model: SubmissionFinalizationResult) -> FinalizedBuild:
    return FinalizedBuild(model.build_id)


def _encode_issues(issues: Sequence[SubmissionAttentionIssue]) -> list[dict[str, object]]:
    return [{"field_id": issue.field_id, "reason": issue.reason.value} for issue in issues]


def _decode_issues(values: Sequence[Mapping[str, object]]) -> tuple[SubmissionAttentionIssue, ...]:
    try:
        return tuple(
            SubmissionAttentionIssue(str(value["field_id"]), SubmissionAttentionReason(str(value["reason"])))
            for value in values
        )
    except (KeyError, ValueError) as error:
        msg = "persisted submission attention issues are invalid"
        raise DataIntegrityError(msg) from error


def _unique_issues(issues: Sequence[SubmissionAttentionIssue]) -> tuple[SubmissionAttentionIssue, ...]:
    return tuple(dict.fromkeys(issues))
