"""Submission ownership and retained-input policy inside an account merge transaction."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import DataIntegrityError
from squid.persistence.account_merge import AccountMergeContext, AccountReference, move_account_references
from squid.persistence.types import now
from squid.submissions.domain import FinalizationJobStatus
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.payload_integrity import submission_payload_digest

SUBMISSION_REFERENCES = (
    AccountReference("builds", "submitter_account_id"),
    AccountReference("submission_drafts", "owner_account_id"),
    AccountReference("submission_drafts", "submission_actor_account_id"),
    AccountReference("submission_finalization_jobs", "requested_by_account_id"),
    AccountReference("submission_inference_runs", "owner_account_id"),
    AccountReference("submission_revision_proposals", "owner_account_id"),
    AccountReference("submission_revision_proposals", "requested_by_account_id"),
    AccountReference("submission_revision_proposals", "approved_by_account_id"),
    AccountReference("submission_schematic_sources", "owner_account_id"),
    AccountReference("submission_schematic_sources", "uploaded_by_account_id"),
    AccountReference("submission_draft_access", "account_id"),
    AccountReference("submission_draft_changes", "actor_account_id"),
    AccountReference("build_schematics", "rights_attested_by_account_id"),
)

# Keep the access row belonging to the draft's pre-merge owner when both accounts have access.
# The joined CASE delete expresses that precedence once, before ownership references move.
_COLLAPSE_DRAFT_ACCESS = """
    DELETE FROM submission_draft_access AS access_row
    WHERE access_row.id IN (
        SELECT CASE
            WHEN draft.owner_account_id = :absorbed THEN survivor_access.id
            ELSE absorbed_access.id
        END
        FROM submission_draft_access AS survivor_access
        JOIN submission_draft_access AS absorbed_access
          ON absorbed_access.draft_id = survivor_access.draft_id
        JOIN submission_drafts AS draft ON draft.id = survivor_access.draft_id
        WHERE survivor_access.account_id = :survivor
          AND absorbed_access.account_id = :absorbed
    )
    """


async def merge_submissions(session: AsyncSession, context: AccountMergeContext) -> None:
    """Move builds, drafts, artifact attestations, and retained finalization owners."""
    affected_drafts = tuple(
        (
            await session.scalars(
                select(SubmissionDraft)
                .where(
                    or_(
                        SubmissionDraft.owner_account_id == context.absorbed,
                        SubmissionDraft.submission_actor_account_id == context.absorbed,
                        SubmissionDraft.id.in_(
                            select(SubmissionFinalizationJob.draft_id).where(
                                SubmissionFinalizationJob.requested_by_account_id == context.absorbed,
                            )
                        ),
                    )
                )
                .order_by(SubmissionDraft.id)
                .with_for_update()
            )
        ).all()
    )
    draft_ids = tuple(draft.id for draft in affected_drafts if draft.owner_account_id == context.absorbed)
    await _canonicalize_finalization_job_owners(session, draft_ids, context.survivor, context.absorbed)
    await session.execute(
        update(SubmissionFinalizationJob)
        .where(
            SubmissionFinalizationJob.requested_by_account_id == context.absorbed,
            SubmissionFinalizationJob.status == FinalizationJobStatus.CLAIMED,
        )
        .values(
            status=FinalizationJobStatus.PENDING,
            claimed_at=None,
            claim_token=None,
            claim_expires_at=None,
            available_at=func.now(),
            updated_at=func.now(),
        )
    )
    await session.execute(text(_COLLAPSE_DRAFT_ACCESS), context.parameters)
    await move_account_references(session, SUBMISSION_REFERENCES, context)


async def _canonicalize_finalization_job_owners(
    session: AsyncSession,
    draft_ids: Sequence[uuid.UUID],
    survivor: int,
    absorbed: int,
) -> None:
    """Rewrite retained payload owners and fence claims that escaped before the merge lock."""
    if not draft_ids:
        return
    jobs = tuple(
        (
            await session.scalars(
                select(SubmissionFinalizationJob)
                .where(
                    SubmissionFinalizationJob.draft_id.in_(draft_ids),
                    SubmissionFinalizationJob.payload.is_not(None),
                )
                .order_by(SubmissionFinalizationJob.draft_id, SubmissionFinalizationJob.id)
                .with_for_update()
            )
        ).all()
    )
    rewritten_at = now()
    for job in jobs:
        payload = job.payload
        if not isinstance(payload, dict):
            msg = "A retained submission finalization payload is not a JSON object."
            raise DataIntegrityError(msg)
        if job.payload_sha256 != submission_payload_digest(payload):
            msg = "A retained submission finalization payload failed its integrity check."
            raise DataIntegrityError(msg)
        owner = payload.get("owner_account_id")
        if not isinstance(owner, int) or isinstance(owner, bool) or owner not in {absorbed, survivor}:
            msg = "A retained submission finalization payload has conflicting account provenance."
            raise DataIntegrityError(msg)
        rewritten = dict(payload)
        rewritten["owner_account_id"] = survivor
        job.payload = rewritten
        job.payload_sha256 = submission_payload_digest(rewritten)
        job.updated_at = rewritten_at
        if job.status is FinalizationJobStatus.CLAIMED:
            job.status = FinalizationJobStatus.PENDING
            job.available_at = rewritten_at
            job.claimed_at = None
            job.claim_token = None
            job.claim_expires_at = None
            job.completed_at = None
            job.attention_at = None
            job.dead_at = None
            job.attention_issues = []
