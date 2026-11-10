"""PostgreSQL persistence for durable submission finalization."""

from collections.abc import Callable, Sequence
from typing import override
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError, ValidationError
from squid.media.domain.jobs import MediaJobStatus
from squid.media.infrastructure.references import media_for_draft
from squid.media.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.schematics.application import SchematicPublication
from squid.schematics.domain.models import SchematicLicense, SchematicVisibility
from squid.schematics.infrastructure.repository import PostgresSchematicRepository
from squid.schematics.infrastructure.wire import decode_analysis
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
from squid.submissions.domain.schematics import DraftSchematicState
from squid.submissions.errors import DraftAccessDeniedError, DraftArtifactsChangedError, DraftNotFoundError
from squid.submissions.infrastructure.finalization_payloads import decode_submission, encode_submission
from squid.submissions.infrastructure.issues import decode_issues, encode_issues
from squid.submissions.infrastructure.repository import _to_stored
from squid.submissions.models.entities import SubmissionDraft
from squid.submissions.models.finalization import (
    SubmissionFinalizationInput,
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
    SubmissionReceiptMedia,
)
from squid.submissions.models.schematics import DraftSchematicLink, DraftSchematicSource
from squid.submissions.payload_integrity import submission_payload_digest

_CLAIM_MINUTES = 5


class PostgresFinalizationJobRepository(FinalizationJobRepository):
    """Update drafts and finalization attempts in the caller's transaction.

    A new submission creates an attempt; worker retries reuse that attempt and
    increment its claim count. Claim tokens prevent replaced workers from saving
    results. The caller must commit before executing claimed work.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None:
        attempts = await self.list_attempts(draft_id, before=None, limit=1)
        return attempts[0] if attempts else None

    @override
    async def get_attempt(self, draft_id: UUID, attempt_id: UUID) -> FinalizationJobSnapshot | None:
        statement = _attempts_for_draft(draft_id).where(SubmissionFinalizationJob.id == attempt_id)
        session = self._session()
        row = (await session.execute(statement)).one_or_none()
        return None if row is None else _snapshot(*row)

    @override
    async def list_attempts(
        self, draft_id: UUID, *, before: int | None, limit: int
    ) -> tuple[FinalizationJobSnapshot, ...]:
        statement = _attempts_for_draft(draft_id).order_by(SubmissionFinalizationJob.attempt_number.desc()).limit(limit)
        if before is not None:
            statement = statement.where(SubmissionFinalizationJob.attempt_number < before)
        session = self._session()
        return tuple(_snapshot(*row) for row in await session.execute(statement))

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
        session = self._session()
        draft_model = await _locked_draft(session, draft.snapshot.id)
        _require_expected_draft(draft_model, draft)
        await _require_current_media(session, draft.snapshot.id, payload.artifacts.normalized_media_upload_ids)
        await _current_schematics(session, draft.snapshot.id, payload.artifacts.schematic_id)
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
        session = self._session()
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
        session = self._session()
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
        """Claim ready or expired work for five minutes, replacing each claim token."""
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
        session = self._session()
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
        await session.flush()
        return tuple(claims)

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
        session = self._session()
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
        session = self._session()
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

    async def lock_claim(self, job: ClaimedFinalizationJob) -> StoredDraft | None:
        """Lock the draft and return it only while the worker token still matches."""
        session = self._session()
        draft = await _locked_draft(session, job.draft_id)
        if await _claimed_job(session, job) is None:
            return None
        return _to_stored(draft)

    async def require_current_media(self, draft_id: UUID, upload_ids: tuple[UUID, ...]) -> None:
        await _require_current_media(self._session(), draft_id, upload_ids)

    async def attach_schematics(self, job: ClaimedFinalizationJob, build_id: int, *, now: Instant) -> None:
        """Attach original files and queue the primary preview within the build transaction."""
        rows = await _current_schematics(self._session(), job.draft_id, job.payload.artifacts.schematic_id)
        policy = job.payload.schematic_policy
        publication = SchematicPublication(
            visibility=SchematicVisibility(policy.visibility.value),
            license=SchematicLicense(policy.license.value) if policy.license is not None else None,
            rights_attested_at=now if policy.rights_attested else None,
            rights_attested_by_account_id=job.payload.owner_account_id if policy.rights_attested else None,
            published_at=now if policy.rights_attested else None,
        )
        repository = PostgresSchematicRepository(self._session)
        # Process the primary last: duplicate source files share a build attachment row.
        for source, _link in sorted(rows, key=lambda row: row[1].is_primary):
            assert source.sha256 is not None and source.analysis is not None
            await repository.record_analysis(
                build_id,
                source.sha256,
                decode_analysis(source.analysis),
                primary=source.id == job.payload.artifacts.schematic_id,
                original_filename=source.filename,
                uploaded_by_account_id=source.uploaded_by_account_id,
                publication=publication,
            )

    async def complete(self, job: ClaimedFinalizationJob, result: FinalizedBuild, *, now: Instant) -> bool:
        """Record the result and retain its media, or return False if the claim was replaced."""
        session = self._session()
        if not await _complete(session, job, result, now=now):
            return False
        await session.flush()
        session.add_all(
            SubmissionReceiptMedia(job_id=job.job_id, upload_id=upload_id, created_at=now)
            for upload_id in job.payload.artifacts.normalized_media_upload_ids
        )
        return True


def _attempts_for_draft(
    draft_id: UUID,
) -> Select[SubmissionFinalizationJob, SubmissionFinalizationInput, SubmissionFinalizationResult]:
    """Read each attempt with its retained input and optional result, scoped to its draft."""
    return (
        select(SubmissionFinalizationJob, SubmissionFinalizationInput, SubmissionFinalizationResult)
        .outerjoin(SubmissionFinalizationInput, SubmissionFinalizationInput.job_id == SubmissionFinalizationJob.id)
        .outerjoin(SubmissionFinalizationResult, SubmissionFinalizationResult.job_id == SubmissionFinalizationJob.id)
        .where(SubmissionFinalizationJob.draft_id == draft_id)
    )


async def _locked_draft(session: AsyncSession, draft_id: UUID) -> SubmissionDraft:
    await lock_uuid(session, draft_id, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
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


async def _complete(
    session: AsyncSession,
    job: ClaimedFinalizationJob,
    result: FinalizedBuild,
    *,
    now: Instant,
) -> bool:
    """Record the created build only if this worker still owns the attempt.

    The caller saves the build in the same transaction, so the result and the
    draft's submitted status cannot outlive a failed build write.
    """
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


async def _current_schematics(
    session: AsyncSession, draft_id: UUID, expected_primary: UUID | None
) -> list[tuple[DraftSchematicSource, DraftSchematicLink]]:
    """Recheck all retained sources while the caller holds the draft lifecycle lock."""
    result = await session.execute(
        select(DraftSchematicSource, DraftSchematicLink)
        .join(DraftSchematicLink, DraftSchematicLink.source_id == DraftSchematicSource.id)
        .where(DraftSchematicLink.draft_id == draft_id, DraftSchematicLink.discarded.is_(False))
        .order_by(DraftSchematicSource.id)
        .with_for_update(of=DraftSchematicSource)
    )
    rows = [(source, link) for source, link in result]
    primary = [source.id for source, link in rows if link.is_primary]
    if (
        any(source.state is not DraftSchematicState.READY or source.analysis is None for source, _ in rows)
        or primary != ([expected_primary] if expected_primary is not None else [])
        or bool(rows) != (expected_primary is not None)
    ):
        raise DraftArtifactsChangedError
    return rows
