"""Submission work that blocks an account deletion, and the drafts the deletion erases.

Submitted builds, review history, and drafts behind them stay with the tombstone
account. Drafts that were never submitted are erased, including ones shared with
editors. Uploaded schematic sources and inference runs are not touched here: the
existing sweeps remove them once no remaining draft refers to them.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.core.submissions.domain import DraftStatus, FinalizationJobStatus
from squid.core.submissions.infrastructure.repository import discard_draft
from squid.core.submissions.models.entities import SubmissionDraft
from squid.core.submissions.models.finalization import SubmissionFinalizationJob
from squid.core.submissions.models.reviews import BuildSubmission

_UNSUBMITTED = (DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION, DraftStatus.EXPIRED)
_UNFINISHED_JOBS = (FinalizationJobStatus.PENDING, FinalizationJobStatus.CLAIMED, FinalizationJobStatus.NEEDS_ATTENTION)


async def erase_unsubmitted_drafts(session: AsyncSession, account_id: int) -> None:
    """Delete the account's drafts that never became a submission."""
    draft_ids = (
        await session.scalars(
            select(SubmissionDraft.id)
            .where(SubmissionDraft.owner_account_id == account_id, SubmissionDraft.status.in_(_UNSUBMITTED))
            .order_by(SubmissionDraft.id)
        )
    ).all()
    for draft_id in draft_ids:
        await lock_uuid(session, draft_id, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
        # Recheck under the lock: the draft may have been submitted since it was listed.
        draft = await session.scalar(
            select(SubmissionDraft)
            .where(SubmissionDraft.id == draft_id, SubmissionDraft.status.in_(_UNSUBMITTED))
            .with_for_update()
        )
        if draft is not None:
            await discard_draft(session, draft)


async def count_unsettled_submissions(session: AsyncSession, account_id: int) -> int:
    """Count submissions that are still being processed or reviewed.

    That is pending builds, pending edit proposals, drafts being processed, and
    submitted drafts whose finalization has not finished.
    """
    counts = (
        select(func.count()).where(
            Build.submitter_account_id == account_id, Build.submission_status == BuildStatus.PENDING
        ),
        select(func.count()).where(
            BuildSubmission.submitter_account_id == account_id,
            BuildSubmission.edit_vote_id.is_not(None),
            BuildSubmission.decision == "pending",
        ),
        select(func.count()).where(
            SubmissionDraft.owner_account_id == account_id, SubmissionDraft.status == DraftStatus.PROCESSING
        ),
        select(func.count()).where(
            SubmissionFinalizationJob.requested_by_account_id == account_id,
            SubmissionFinalizationJob.status.in_(_UNFINISHED_JOBS),
        ),
    )
    row = (await session.execute(select(*(count.scalar_subquery() for count in counts)))).one()
    return sum(row)
