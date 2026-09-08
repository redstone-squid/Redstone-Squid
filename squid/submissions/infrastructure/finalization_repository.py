"""PostgreSQL persistence for durable submission finalization."""

from collections.abc import Sequence
from typing import override
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError, ValidationError
from squid.media.application.jobs import MediaJobStatus
from squid.media.infrastructure.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.media.infrastructure.references import media_for_draft
from squid.persistence.advisory_locks import SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE, lock_uuid
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.finalization import (
    MAX_FINALIZATION_JOB_CLAIM,
    ClaimedFinalizationJob,
    DraftPreparationSnapshot,
    FinalizationFailureOutcome,
    FinalizationJobRepository,
    FinalizationJobSnapshot,
    SubmissionRequestResult,
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
    SubmissionFinalizationInput,
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
)
from squid.submissions.infrastructure.finalization_payloads import decode_submission, encode_submission
from squid.submissions.infrastructure.issues import decode_issues, encode_issues
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import _to_stored
from squid.submissions.payload_integrity import submission_payload_digest

_CLAIM_MINUTES = 5


class PostgresFinalizationJobRepository(FinalizationJobRepository):
    """Atomically coordinate draft state and UUID-fenced finalization jobs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SubmissionFinalizationJob, SubmissionFinalizationInput, SubmissionFinalizationResult)
                    .outerjoin(
                        SubmissionFinalizationInput,
                        SubmissionFinalizationInput.job_id == SubmissionFinalizationJob.id,
                    )
                    .outerjoin(
                        SubmissionFinalizationResult,
                        SubmissionFinalizationResult.job_id == SubmissionFinalizationJob.id,
                    )
                    .where(SubmissionFinalizationJob.draft_id == draft_id)
                    .order_by(SubmissionFinalizationJob.attempt_number.desc())
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return None
            job, original_input, result = row
        return _snapshot(job, original_input, result)

    @override
    async def get_attempt(self, draft_id: UUID, attempt_id: UUID) -> FinalizationJobSnapshot | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SubmissionFinalizationJob, SubmissionFinalizationInput, SubmissionFinalizationResult)
                    .outerjoin(
                        SubmissionFinalizationInput,
                        SubmissionFinalizationInput.job_id == SubmissionFinalizationJob.id,
                    )
                    .outerjoin(
                        SubmissionFinalizationResult,
                        SubmissionFinalizationResult.job_id == SubmissionFinalizationJob.id,
                    )
                    .where(
                        SubmissionFinalizationJob.draft_id == draft_id,
                        SubmissionFinalizationJob.id == attempt_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            return _snapshot(*row)

    @override
    async def list_attempts(
        self, draft_id: UUID, *, before: int | None, limit: int
    ) -> tuple[FinalizationJobSnapshot, ...]:
        statement = (
            select(SubmissionFinalizationJob, SubmissionFinalizationInput, SubmissionFinalizationResult)
            .outerjoin(
                SubmissionFinalizationInput,
                SubmissionFinalizationInput.job_id == SubmissionFinalizationJob.id,
            )
            .outerjoin(
                SubmissionFinalizationResult, SubmissionFinalizationResult.job_id == SubmissionFinalizationJob.id
            )
            .where(SubmissionFinalizationJob.draft_id == draft_id)
            .order_by(SubmissionFinalizationJob.attempt_number.desc())
            .limit(limit)
        )
        if before is not None:
            statement = statement.where(SubmissionFinalizationJob.attempt_number < before)
        async with self._session_factory() as session:
            return tuple(
                _snapshot(job, original_input, result)
                for job, original_input, result in await session.execute(statement)
            )

    @override
    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
        actor_account_id: int | None = None,
    ) -> FinalizationJobSnapshot:
        """Append a pending attempt and transition its draft in one transaction."""
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
                original_input = await session.get(SubmissionFinalizationInput, job.id)
                result = await session.get(SubmissionFinalizationResult, job.id)
                return _snapshot(job, original_input, result)
            if status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                msg = f"drafts in {status.value} state cannot be finalized"
                raise ValidationError(msg, resource="submission_draft")

            draft_model.preparation_issues = []
            draft_model.preparation_retry_at = None
            draft_model.status = DraftStatus.PROCESSING
            draft_model.updated_at = now
            draft_model.expires_at = expires_at
            job = SubmissionFinalizationJob(
                draft_id=draft.snapshot.id,
                attempt_number=1 if job is None else job.attempt_number + 1,
                requested_by_account_id=actor_account_id or draft.snapshot.owner_account_id,
                draft_revision=draft.snapshot.revision,
                payload=encoded,
                payload_sha256=digest,
                status=FinalizationJobStatus.PENDING,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            await session.flush()
            original_input = SubmissionFinalizationInput(
                job_id=job.id,
                payload=encoded,
                payload_sha256=digest,
                created_at=now,
            )
            session.add(original_input)
        return _snapshot(job, original_input, None)

    @override
    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
        waiting_for_artifacts: bool = False,
        actor_account_id: int | None = None,
    ) -> SubmissionRequestResult:
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
                original_input = await session.get(SubmissionFinalizationInput, job.id)
                result = await session.get(SubmissionFinalizationResult, job.id)
                return _snapshot(job, original_input, result)
            if status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                msg = f"drafts in {status.value} state cannot request finalization"
                raise ValidationError(msg, resource="submission_draft")

            draft_model.status = DraftStatus.NEEDS_ATTENTION
            draft_model.updated_at = now
            draft_model.expires_at = expires_at
            draft_model.preparation_issues = encode_issues(normalized_issues)
            draft_model.preparation_retry_at = now.add(seconds=30) if waiting_for_artifacts else None
            draft_model.submission_actor_account_id = actor_account_id or draft.snapshot.owner_account_id
        return DraftPreparationSnapshot(
            draft.snapshot.id, draft.snapshot.revision, normalized_issues, waiting_for_artifacts
        )

    @override
    async def reserve_preparations(self, *, now: Instant, limit: int) -> tuple[StoredDraft, ...]:
        """Reserve bounded attachment rechecks; abandoned reservations become due again."""
        if not 1 <= limit <= MAX_FINALIZATION_JOB_CLAIM:
            message = "Invalid preparation batch limit."
            raise ValueError(message)
        async with self._session_factory.begin() as session:
            drafts = tuple(
                await session.scalars(
                    select(SubmissionDraft)
                    .where(
                        SubmissionDraft.preparation_retry_at <= now,
                        SubmissionDraft.expires_at > now,
                        SubmissionDraft.status == DraftStatus.NEEDS_ATTENTION,
                    )
                    .order_by(SubmissionDraft.preparation_retry_at, SubmissionDraft.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for draft in drafts:
                draft.preparation_retry_at = now.add(seconds=30)
            return tuple(_to_stored(draft) for draft in drafts)

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
            rows = tuple(
                (
                    await session.execute(
                        select(SubmissionFinalizationJob, SubmissionFinalizationInput)
                        .outerjoin(
                            SubmissionFinalizationInput,
                            SubmissionFinalizationInput.job_id == SubmissionFinalizationJob.id,
                        )
                        .where(ready)
                        .order_by(SubmissionFinalizationJob.available_at, SubmissionFinalizationJob.id)
                        .limit(limit)
                        .with_for_update(of=SubmissionFinalizationJob, skip_locked=True)
                    )
                ).all()
            )
            claims: list[ClaimedFinalizationJob] = []
            for job, original_input in rows:
                payload = job.payload
                if not isinstance(payload, dict):
                    msg = "claimable finalization job has no JSON object payload"
                    raise DataIntegrityError(msg)
                if job.payload_sha256 != submission_payload_digest(payload):
                    msg = "claimable finalization job failed its payload integrity check"
                    raise DataIntegrityError(msg)
                if original_input is None:
                    msg = "claimable finalization job has no retained original input"
                    raise DataIntegrityError(msg)
                _input_digest(original_input)
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
                        requested_by_account_id=job.requested_by_account_id,
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
            return await complete_in_session(session, job, result, now=now)

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
            model.attention_issues = encode_issues(normalized_issues)
            _clear_claim(model)
            model.updated_at = now
            draft.preparation_issues = model.attention_issues
            draft.preparation_retry_at = None
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
                model.attention_issues = encode_issues((issue,))
                draft.preparation_issues = model.attention_issues
                draft.preparation_retry_at = None
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
                .where(media_for_draft(draft_id))
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
        select(SubmissionFinalizationJob)
        .where(SubmissionFinalizationJob.draft_id == draft_id)
        .order_by(SubmissionFinalizationJob.attempt_number.desc())
        .limit(1)
        .with_for_update()
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
            SubmissionFinalizationJob.requested_by_account_id == claim.requested_by_account_id,
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


def _clear_claim(job: SubmissionFinalizationJob) -> None:
    job.claimed_at = None
    job.claim_token = None
    job.claim_expires_at = None


def _snapshot(
    job: SubmissionFinalizationJob,
    original_input: SubmissionFinalizationInput | None,
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
        issues=decode_issues(job.attention_issues),
        result=_result(result) if result is not None else None,
        attempt_number=job.attempt_number,
        input_sha256=_input_digest(original_input),
    )


def _input_digest(original_input: SubmissionFinalizationInput | None) -> str | None:
    if original_input is None:
        return None
    if original_input.payload_sha256 != submission_payload_digest(original_input.payload):
        msg = "retained submission finalization input failed its integrity check"
        raise DataIntegrityError(msg)
    return original_input.payload_sha256


def _result(model: SubmissionFinalizationResult) -> FinalizedBuild:
    return FinalizedBuild(model.build_id)


def _unique_issues(issues: Sequence[SubmissionAttentionIssue]) -> tuple[SubmissionAttentionIssue, ...]:
    return tuple(dict.fromkeys(issues))


async def complete_in_session(
    session: AsyncSession,
    job: ClaimedFinalizationJob,
    result: FinalizedBuild,
    *,
    now: Instant,
) -> bool:
    """Fence and finish a receipt within the transaction that creates its build."""
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
