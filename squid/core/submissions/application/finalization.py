"""Turn completed submission drafts into queued build creation work.

SubmissionFinalizationService validates the draft's form answers and checks its
attachments before storing a finalization attempt. SubmissionFinalizationWorker
claims that attempt and delegates build creation to a SubmissionExecutor. The
stored attempt survives restarts, so clients can retrieve its progress or result.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.actors import ActingContext, Actor, AnonymousActor, UserActor
from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.errors import PermissionDeniedError
from squid.core.submissions.application.drafts import (
    DEFAULT_DRAFT_RETENTION_DAYS,
    DraftUnitOfWork,
    StoredDraft,
    SubmissionDraftService,
    draft_actor_id,
)
from squid.core.submissions.application.preparation import (
    PreparationRejected,
    PreparationWaiting,
    SubmissionPreparation,
)
from squid.core.submissions.domain import DraftRevisionConflictError, DraftStatus
from squid.core.submissions.domain.build_edit import DraftEditResult
from squid.core.submissions.domain.finalization import (
    BuildSubmissionRejected,
    BuildSubmissionResult,
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.core.submissions.errors import (
    DraftAccessDeniedError,
    DraftNotFoundError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
    DraftValidationError,
)
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.validation import RuleIssue
from squid.core.worker_context import worker_execution, worker_scope
from squid_i18n import md_tr

MAX_FINALIZATION_JOB_CLAIM = 32
DEFAULT_FINALIZATION_ATTEMPTS = 3
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedFinalizationJob:
    """A queued finalization attempt and the token identifying its current worker."""

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
            msg = tr(
                t"submissions-claimed-finalization-metadata-invalid "
                t"= claimed submission finalization metadata is invalid"
            )
            raise DataIntegrityError(msg)


@dataclass(frozen=True, slots=True)
class FinalizationJobSnapshot:
    """Progress and outcome of one submission attempt.

    attempt_number identifies successive submissions of the same draft, such as
    resubmitting after correcting an issue. attempts counts worker claims of
    this particular attempt, including retries after failures.
    """

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
    submission_id: UUID | None = None
    input_sha256: str | None = None


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
    """Create a build for a claimed finalization attempt.

    Success means the build, its attachment references, and the result linking
    the attempt to that build have committed together. Return None if another
    worker has replaced the claim, or a rejection if the draft needs correction.
    """

    async def execute(
        self,
        job: ClaimedFinalizationJob,
        *,
        now: Instant,
        context: ActingContext,
    ) -> BuildSubmissionResult | None: ...


class FinalizationJobRepository(Protocol):
    """Store finalization attempts and update their source drafts together.

    Each attempt retains the prepared submission for workers to retry. Claims
    carry a token so a replaced worker cannot update the attempt's outcome.
    The caller commits these operations before executing claimed work.
    """

    async def get(self, draft_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def get_attempt(self, draft_id: UUID, attempt_id: UUID) -> FinalizationJobSnapshot | None: ...

    async def list_attempts(
        self, draft_id: UUID, *, selector: PageSelector, page_size: int
    ) -> Page[FinalizationJobSnapshot]: ...

    async def enqueue(
        self,
        draft: StoredDraft,
        payload: NormalizedSubmission,
        *,
        now: Instant,
        expires_at: Instant,
        actor_account_id: int,
    ) -> FinalizationJobSnapshot: ...

    async def record_preparation_attention(
        self,
        draft: StoredDraft,
        issues: Sequence[SubmissionAttentionIssue],
        *,
        now: Instant,
        expires_at: Instant,
        waiting_for_artifacts: bool = False,
        actor_account_id: int | None,
    ) -> SubmissionRequestResult: ...

    async def reserve_preparations(self, *, now: Instant, limit: int) -> tuple[StoredDraft, ...]: ...

    async def claim(self, *, now: Instant, limit: int) -> Sequence[ClaimedFinalizationJob]: ...

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


class DraftEditor(Protocol):
    async def create(
        self, build_id: int, category: str, capabilities: frozenset[str], locale: str | None, *, context: ActingContext
    ) -> StoredDraft: ...
    async def finish(self, draft_id: UUID, locale: str | None, *, context: ActingContext) -> DraftEditResult: ...


class SubmissionFinalizationService:
    """Check a submission draft and queue it for build creation.

    Form validation checks the answers; preparation resolves sponsor details
    and attachment readiness. Problems keep the draft editable for correction.
    If only attachment processing remains, the worker rechecks preparation
    automatically. A ready submission becomes a stored finalization attempt.
    """

    def __init__(
        self,
        drafts: SubmissionDraftService,
        preparation: SubmissionPreparation,
        *,
        uow_factory: UnitOfWorkFactory[FinalizationUnitOfWork],
        editor: DraftEditor | None = None,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"submissions-attention-retention-positive = finalization attention retention must be positive")
            raise InvalidStateError(msg)
        self.editor = editor
        self._drafts = drafts
        self._preparation = preparation
        self._uow_factory = uow_factory
        self._retention_days = retention_days

    async def submit(
        self, draft_id: UUID, *, context: ActingContext, locale: str | None, now: Instant | None = None
    ) -> SubmissionRequestResult | DraftEditResult:
        """Queue a validated draft or retain the issues that prevent finalization.

        Repeated requests return the active job or completed result. If only
        artifact processing remains, retain a wait that the worker can recheck.
        Other preparation issues are retained for correction before resubmission.
        """
        actor = context.effective
        with context.scope():
            current = await self._drafts.get_accessible(draft_id, context=context)
            if current.edit_source is not None:
                if self.editor is None:
                    raise InvalidStateError
                return await self.editor.finish(draft_id, locale, context=context)
            return await self._submit(
                current, actor, locale=locale, now=now or Instant.now(), refresh_retention=True, context=context
            )

    async def _submit(
        self,
        current: StoredDraft,
        actor: Actor,
        *,
        locale: str | None,
        now: Instant,
        refresh_retention: bool,
        context: ActingContext,
    ) -> SubmissionRequestResult:
        draft_id = current.snapshot.id
        if current.snapshot.status is DraftStatus.PROCESSING:
            async with self._uow_factory() as uow:
                existing = await uow.finalization_jobs.get(draft_id)
            if existing is not None and existing.status in {
                FinalizationJobStatus.PENDING,
                FinalizationJobStatus.CLAIMED,
            }:
                return existing
            msg = tr(t"submissions-finalization-job-missing = processing draft has no active finalization job")
            raise InvalidStateError(msg)
        if current.snapshot.status is DraftStatus.SUBMITTED:
            async with self._uow_factory() as uow:
                existing = await uow.finalization_jobs.get(draft_id)
            if existing is None or existing.status is not FinalizationJobStatus.COMPLETED:
                msg = tr(
                    t"submissions-finalization-result-missing = submitted draft has no retained finalization result"
                )
                raise InvalidStateError(msg)
            return existing

        touched_at = now
        expires_at = (
            touched_at.add(days=self._retention_days, days_assumed_24h_ok=True)
            if refresh_retention
            else current.expires_at
        )
        try:
            async with self._uow_factory() as uow:
                latest = await self._drafts.get_accessible_in(uow, draft_id, actor)
            validated = await self._drafts.validate_snapshot(latest, locale=locale, context=context)
            if validated.draft.snapshot.revision != current.snapshot.revision:
                raise DraftRevisionConflictError(
                    expected=current.snapshot.revision, current=validated.draft.snapshot.revision
                )
        except DraftValidationError as error:
            issues = _manifest_issues(error)
            async with self._uow_factory() as uow:
                await self._drafts.get_accessible_in(uow, draft_id, actor)
                result = await uow.finalization_jobs.record_preparation_attention(
                    current,
                    issues,
                    now=touched_at,
                    expires_at=expires_at,
                    actor_account_id=draft_actor_id(actor),
                )
                await uow.commit()
                return result
        except DraftSchemaUnsupportedError:
            async with self._uow_factory() as uow:
                await self._drafts.get_accessible_in(uow, draft_id, actor)
                result = await uow.finalization_jobs.record_preparation_attention(
                    current,
                    (SubmissionAttentionIssue("submission", SubmissionAttentionReason.SCHEMA_UNSUPPORTED),),
                    now=touched_at,
                    expires_at=expires_at,
                    actor_account_id=draft_actor_id(actor),
                )
                await uow.commit()
                return result

        preparation = await self._preparation.prepare(validated, context=context)
        if isinstance(preparation, PreparationRejected | PreparationWaiting):
            async with self._uow_factory() as uow:
                await self._drafts.get_accessible_in(uow, draft_id, actor)
                result = await uow.finalization_jobs.record_preparation_attention(
                    validated.draft,
                    preparation.issues,
                    now=touched_at,
                    expires_at=expires_at,
                    waiting_for_artifacts=isinstance(preparation, PreparationWaiting),
                    actor_account_id=draft_actor_id(actor),
                )
                await uow.commit()
                return result
        async with self._uow_factory() as uow:
            await self._drafts.get_accessible_in(uow, draft_id, actor)
            result = await uow.finalization_jobs.enqueue(
                validated.draft,
                preparation.value,
                now=touched_at,
                expires_at=expires_at,
                actor_account_id=draft_actor_id(actor),
            )
            await uow.commit()
            return result

    async def resume_waiting(self, *, context: ActingContext, now: Instant, limit: int = 8) -> None:
        """Recheck reserved attachment waits without renewing their retention period."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                drafts = await uow.finalization_jobs.reserve_preparations(now=now, limit=limit)
                await uow.commit()
            for draft in drafts:
                with worker_execution("submission-preparation", draft.preparation_attempt) as attempt:
                    try:
                        requester = (
                            UserActor(account_id=draft.submission_actor_account_id)
                            if draft.submission_actor_account_id is not None
                            else AnonymousActor()
                        )
                        await self._submit(
                            draft,
                            requester,
                            locale=None,
                            now=now,
                            refresh_retention=False,
                            context=attempt,
                        )
                    except DraftAccessDeniedError, PermissionDeniedError:
                        async with self._uow_factory() as uow:
                            await uow.finalization_jobs.record_preparation_attention(
                                draft,
                                (SubmissionAttentionIssue("submission", SubmissionAttentionReason.PERMISSION_REVOKED),),
                                now=now,
                                expires_at=draft.expires_at,
                                actor_account_id=draft.submission_actor_account_id,
                            )
                            await uow.commit()
                    except DraftRevisionConflictError, DraftNotFoundError, DraftStateConflictError:
                        # Edits, deletion, expiry, or account merges invalidate this reservation.
                        continue

    async def status(
        self, draft_id: UUID, *, context: ActingContext
    ) -> SubmissionRequestResult | DraftEditResult | None:
        """Return retained finalization state after checking owner or staff access."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                draft = await self._drafts.get_accessible_in(uow, draft_id, actor)
                if draft.edit_source is not None:
                    return draft.edit_source.result
                if draft.preparation_issues:
                    return DraftPreparationSnapshot(
                        draft_id,
                        draft.snapshot.revision,
                        draft.preparation_issues,
                        draft.preparation_retry_at is not None,
                    )
                return await uow.finalization_jobs.get(draft_id)

    async def attempt(
        self, draft_id: UUID, attempt_id: UUID, *, context: ActingContext
    ) -> FinalizationJobSnapshot | None:
        """Read a retained attempt only within its accessible parent draft."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                await self._drafts.get_accessible_in(uow, draft_id, actor)
                return await uow.finalization_jobs.get_attempt(draft_id, attempt_id)

    async def attempts(
        self, draft_id: UUID, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[FinalizationJobSnapshot]:
        """Page attempt history newest first."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                await self._drafts.get_accessible_in(uow, draft_id, actor)
                return await uow.finalization_jobs.list_attempts(draft_id, selector=selector, page_size=page_size)


class SubmissionFinalizationWorker:
    """Run bounded finalization batches with retry and dead-letter handling."""

    def __init__(
        self,
        executor: SubmissionExecutor,
        *,
        uow_factory: UnitOfWorkFactory[FinalizationUnitOfWork],
        max_attempts: int = DEFAULT_FINALIZATION_ATTEMPTS,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
        preparation: SubmissionFinalizationService | None = None,
    ) -> None:
        if max_attempts < 1 or retention_days < 1:
            msg = tr(t"submissions-retry-retention-limits = finalization retry and retention limits must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._executor = executor
        self._max_attempts = max_attempts
        self._retention_days = retention_days
        self._preparation = preparation

    async def process_batch(self, *, context: ActingContext, limit: int = 8, now: Instant | None = None) -> None:
        """Recheck attachment waits, then claim and process up to limit jobs in order.

        Commit claims before running the executor. Unexpected exceptions are
        recorded for retry; cancellations leave the lease to expire. Successful
        execution has already committed its build and receipt.
        """
        with worker_scope(context):
            if not 1 <= limit <= MAX_FINALIZATION_JOB_CLAIM:
                maximum = MAX_FINALIZATION_JOB_CLAIM
                raise InvalidStateError(
                    md_tr(
                        t"submissions-claim-limit = finalization claim limit must be between 1 and {maximum}",
                    )
                )
            claimed_at = now or Instant.now()
            if self._preparation is not None:
                await self._preparation.resume_waiting(now=claimed_at, limit=limit, context=context)
            async with self._uow_factory() as uow:
                jobs = await uow.finalization_jobs.claim(now=claimed_at, limit=limit)
                await uow.commit()
            for job in jobs:
                with worker_execution("submission-finalization", job.claim_token) as attempt:
                    await self._process(job, now=claimed_at, context=attempt)

    async def _process(self, job: ClaimedFinalizationJob, *, now: Instant, context: ActingContext) -> None:
        expires_at = now.add(days=self._retention_days, days_assumed_24h_ok=True)
        try:
            result = await self._executor.execute(job, now=now, context=context)
        except Exception as error:
            logger.exception("Submission finalization failed for draft %s, job %s", job.draft_id, job.job_id)
            retry_at = now.add(seconds=_retry_delay(job.attempts))
            async with self._uow_factory() as uow:
                await uow.finalization_jobs.fail(
                    job,
                    type(error).__name__,
                    now=now,
                    retry_at=retry_at,
                    expires_at=expires_at,
                    max_attempts=self._max_attempts,
                )
                await uow.commit()
            return

        if isinstance(result, BuildSubmissionRejected):
            async with self._uow_factory() as uow:
                await uow.finalization_jobs.needs_attention(
                    job,
                    result.issues,
                    now=now,
                    expires_at=expires_at,
                )
                await uow.commit()
            return


def _manifest_issues(error: DraftValidationError) -> tuple[SubmissionAttentionIssue, ...]:
    issues: list[SubmissionAttentionIssue] = []
    for issue in error.field_issues:
        try:
            attention = SubmissionAttentionIssue(
                issue.field,
                SubmissionAttentionReason(issue.params.reason if isinstance(issue, RuleIssue) else issue.code.value),
            )
        except ValueError:
            attention = SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED)
        if attention not in issues:
            issues.append(attention)
    return tuple(issues) or (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)


def _retry_delay(attempts: int) -> int:
    return min(300, 2 ** min(attempts, 8))


class FinalizationUnitOfWork(DraftUnitOfWork, Protocol):
    @property
    def finalization_jobs(self) -> FinalizationJobRepository: ...
