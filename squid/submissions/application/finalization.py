"""Durable submission finalization, driven the same way by the bot and the API."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError, JSONValue
from squid.core.i18n import tr
from squid.submissions.application.drafts import (
    DEFAULT_DRAFT_RETENTION_DAYS,
    DraftActor,
    StoredDraft,
    SubmissionDraftService,
    draft_actor_id,
)
from squid.submissions.application.preparation import PreparationRejected, PreparationWaiting, SubmissionPreparation
from squid.submissions.domain import DraftRevisionConflictError, DraftStatus
from squid.submissions.domain.finalization import (
    BuildSubmissionRejected,
    BuildSubmissionResult,
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftNotFoundError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
    DraftValidationError,
)

MAX_FINALIZATION_JOB_CLAIM = 32
DEFAULT_FINALIZATION_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ClaimedFinalizationJob:
    """One durable job fenced to a worker by an unguessable claim UUID."""

    job_id: UUID
    draft_id: UUID
    draft_revision: int
    payload: NormalizedSubmission
    attempts: int
    claimed_at: Instant
    claim_token: UUID
    requested_by_account_id: int | None = None

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.claim_token.int == 0 or self.attempts < 1:
            msg = tr(t"claimed submission finalization metadata is invalid")
            raise DataIntegrityError(msg)


@dataclass(frozen=True, slots=True)
class FinalizationJobSnapshot:
    """Current durable state and retained outcome for a source draft."""

    job_id: UUID
    draft_id: UUID
    draft_revision: int
    status: FinalizationJobStatus
    attempts: int
    available_at: Instant
    claimed_at: Instant | None = None
    claim_token: UUID | None = None
    completed_at: Instant | None = None
    attention_at: Instant | None = None
    dead_at: Instant | None = None
    last_error: str | None = None
    issues: tuple[SubmissionAttentionIssue, ...] = ()
    result: FinalizedBuild | None = None
    attempt_number: int = 1


@dataclass(frozen=True, slots=True)
class FinalizationFailureOutcome:
    """Result of an unexpected failure transition guarded by a claim token."""

    applied: bool
    dead: bool


@dataclass(frozen=True, slots=True)
class DraftPreparationSnapshot:
    """Draft issues recorded before an executable attempt exists."""

    draft_id: UUID
    draft_revision: int
    issues: tuple[SubmissionAttentionIssue, ...]
    waiting_for_artifacts: bool = False

    @property
    def status(self) -> DraftStatus:
        return DraftStatus.NEEDS_ATTENTION


type SubmissionRequestResult = FinalizationJobSnapshot | DraftPreparationSnapshot


class SubmissionExecutor(Protocol):
    """Commit build, artifacts, and receipt under the claim fence before returning success."""

    async def execute(
        self,
        job: ClaimedFinalizationJob,
        *,
        now: Instant,
    ) -> BuildSubmissionResult | None: ...


class FinalizationJobRepository(Protocol):
    """Atomic draft transitions and durable claim-token-fenced queue operations."""

    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def get_attempt(self, draft_id: UUID, attempt_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def list_attempts(
        self, draft_id: UUID, *, before: int | None, limit: int
    ) -> tuple[FinalizationJobSnapshot, ...]: ...

    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
        actor_account_id: int | None = None,
    ) -> FinalizationJobSnapshot: ...

    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
        waiting_for_artifacts: bool = False,
        actor_account_id: int | None = None,
    ) -> SubmissionRequestResult: ...

    async def reserve_preparations(self, *, now: Instant, limit: int) -> tuple[StoredDraft, ...]: ...

    async def claim(self, *, now: Instant, limit: int) -> Sequence[ClaimedFinalizationJob]: ...

    async def complete(
        self,
        job: ClaimedFinalizationJob,
        result: FinalizedBuild,
        *,
        now: Instant,
    ) -> bool: ...

    async def needs_attention(
        self,
        job: ClaimedFinalizationJob,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
    ) -> bool: ...

    async def fail(
        self,
        job: ClaimedFinalizationJob,
        error: str,
        *,
        now: Instant,
        retry_at: Instant,
        expires_at: Instant,
        max_attempts: int,
    ) -> FinalizationFailureOutcome: ...


class SubmissionFinalizationService:
    """Coordinate manifest validation, preparation, and durable enqueueing."""

    def __init__(
        self,
        drafts: SubmissionDraftService,
        preparation: SubmissionPreparation,
        jobs: FinalizationJobRepository,
        *,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"finalization attention retention must be positive")
            raise InvalidStateError(msg)
        self._drafts = drafts
        self._preparation = preparation
        self._jobs = jobs
        self._retention_days = retention_days

    async def submit(
        self,
        draft_id: UUID,
        account_id: DraftActor,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> SubmissionRequestResult:
        """Start idempotent processing or persist actionable preparation issues."""
        current = await self._drafts.get_accessible(draft_id, account_id)
        return await self._submit(current, account_id, locale=locale, now=now or Instant.now(), refresh_retention=True)

    async def _submit(
        self, current: StoredDraft, account_id: DraftActor, *, locale: str | None, now: Instant, refresh_retention: bool
    ) -> SubmissionRequestResult:
        draft_id = current.snapshot.id
        if current.snapshot.status is DraftStatus.PROCESSING:
            existing = await self._jobs.get(draft_id)
            if existing is not None and existing.status in {
                FinalizationJobStatus.PENDING,
                FinalizationJobStatus.CLAIMED,
            }:
                return existing
            msg = tr(t"processing draft has no active finalization job")
            raise InvalidStateError(msg)
        if current.snapshot.status is DraftStatus.SUBMITTED:
            existing = await self._jobs.get(draft_id)
            if existing is None or existing.status is not FinalizationJobStatus.COMPLETED:
                msg = tr(t"submitted draft has no retained finalization result")
                raise InvalidStateError(msg)
            return existing

        touched_at = now
        expires_at = (
            touched_at.add(days=self._retention_days, days_assumed_24h_ok=True)
            if refresh_retention
            else current.expires_at
        )
        try:
            validated = await self._drafts.validate_for_finalization(draft_id, account_id, locale=locale)
            if validated.draft.snapshot.revision != current.snapshot.revision:
                raise DraftRevisionConflictError(
                    expected=current.snapshot.revision, actual=validated.draft.snapshot.revision
                )
        except DraftValidationError as error:
            issues = _manifest_issues(error.public_context)
            return await self._jobs.record_preparation_attention(
                current,
                issues,
                now=touched_at,
                expires_at=expires_at,
                actor_account_id=draft_actor_id(account_id),
            )
        except DraftSchemaUnsupportedError:
            return await self._jobs.record_preparation_attention(
                current,
                (SubmissionAttentionIssue("submission", SubmissionAttentionReason.SCHEMA_UNSUPPORTED),),
                now=touched_at,
                expires_at=expires_at,
                actor_account_id=draft_actor_id(account_id),
            )

        preparation = await self._preparation.prepare(validated)
        if isinstance(preparation, PreparationRejected | PreparationWaiting):
            return await self._jobs.record_preparation_attention(
                validated.draft,
                preparation.issues,
                now=touched_at,
                expires_at=expires_at,
                waiting_for_artifacts=isinstance(preparation, PreparationWaiting),
                actor_account_id=draft_actor_id(account_id),
            )
        return await self._jobs.enqueue(
            validated.draft,
            preparation.value,
            now=touched_at,
            expires_at=expires_at,
            actor_account_id=draft_actor_id(account_id),
        )

    async def resume_waiting(self, *, now: Instant, limit: int = 8) -> None:
        """Recheck reserved attachment waits without renewing their retention period."""
        for draft in await self._jobs.reserve_preparations(now=now, limit=limit):
            try:
                await self._submit(
                    draft,
                    draft.submission_actor_account_id or draft.snapshot.owner_account_id,
                    locale=None,
                    now=now,
                    refresh_retention=False,
                )
            except DraftAccessDeniedError:
                await self._jobs.record_preparation_attention(
                    draft,
                    (SubmissionAttentionIssue("submission", SubmissionAttentionReason.PERMISSION_REVOKED),),
                    now=now,
                    expires_at=draft.expires_at,
                    actor_account_id=draft.submission_actor_account_id,
                )
            except DraftRevisionConflictError, DraftNotFoundError, DraftStateConflictError:
                # Edits, deletion, expiry, or account merges invalidate this reservation.
                continue

    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        """Return retained finalization state after rechecking draft ownership."""
        draft = await self._drafts.get_accessible(draft_id, account_id)
        if draft.preparation_issues:
            return DraftPreparationSnapshot(
                draft_id, draft.snapshot.revision, draft.preparation_issues, draft.preparation_retry_at is not None
            )
        return await self._jobs.get(draft_id)

    async def attempt(self, draft_id: UUID, account_id: DraftActor, attempt_id: UUID) -> FinalizationJobSnapshot | None:
        """Read a retained attempt only within its accessible parent draft."""
        await self._drafts.get_accessible(draft_id, account_id)
        return await self._jobs.get_attempt(draft_id, attempt_id)

    async def attempts(
        self, draft_id: UUID, account_id: DraftActor, *, before: int | None = None, limit: int = 20
    ) -> tuple[FinalizationJobSnapshot, ...]:
        """Read newest-first history with a stable exclusive attempt-number cursor."""
        if not 1 <= limit <= 100 or (before is not None and before < 1):
            message = "Invalid attempt history cursor or limit."
            raise InvalidStateError(message)
        await self._drafts.get_accessible(draft_id, account_id)
        return await self._jobs.list_attempts(draft_id, before=before, limit=limit)


class SubmissionFinalizationWorker:
    """Run bounded finalization batches with retry and dead-letter handling."""

    def __init__(
        self,
        jobs: FinalizationJobRepository,
        executor: SubmissionExecutor,
        *,
        max_attempts: int = DEFAULT_FINALIZATION_ATTEMPTS,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
        preparation: SubmissionFinalizationService | None = None,
    ) -> None:
        if max_attempts < 1 or retention_days < 1:
            msg = tr(t"finalization retry and retention limits must be positive")
            raise InvalidStateError(msg)
        self._jobs = jobs
        self._executor = executor
        self._max_attempts = max_attempts
        self._retention_days = retention_days
        self._preparation = preparation

    async def process_batch(self, *, limit: int = 8, now: Instant | None = None) -> None:
        """Claim and process at most ``limit`` jobs sequentially."""
        if not 1 <= limit <= MAX_FINALIZATION_JOB_CLAIM:
            maximum = MAX_FINALIZATION_JOB_CLAIM
            raise InvalidStateError(tr(t"finalization claim limit must be between 1 and {maximum}"))
        claimed_at = now or Instant.now()
        if self._preparation is not None:
            await self._preparation.resume_waiting(now=claimed_at, limit=limit)
        for job in await self._jobs.claim(now=claimed_at, limit=limit):
            await self._process(job, now=claimed_at)

    async def _process(self, job: ClaimedFinalizationJob, *, now: Instant) -> None:
        expires_at = now.add(days=self._retention_days, days_assumed_24h_ok=True)
        try:
            result = await self._executor.execute(job, now=now)
        except Exception as error:
            retry_at = now.add(seconds=_retry_delay(job.attempts))
            await self._jobs.fail(
                job,
                type(error).__name__,
                now=now,
                retry_at=retry_at,
                expires_at=expires_at,
                max_attempts=self._max_attempts,
            )
            return

        if isinstance(result, BuildSubmissionRejected):
            await self._jobs.needs_attention(
                job,
                result.issues,
                now=now,
                expires_at=expires_at,
            )
            return


def _manifest_issues(context: Mapping[str, JSONValue]) -> tuple[SubmissionAttentionIssue, ...]:
    field_errors = context.get("field_errors")
    if not isinstance(field_errors, Mapping):
        return (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)
    issues: list[SubmissionAttentionIssue] = []
    for field_id, reason in sorted(field_errors.items()):
        if not isinstance(reason, str):
            continue
        try:
            issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason(reason)))
        except ValueError:
            issues.append(SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED))
    return tuple(issues) or (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)


def _retry_delay(attempts: int) -> int:
    return min(300, 2 ** min(attempts, 8))
