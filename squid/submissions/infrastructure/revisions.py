"""PostgreSQL revision proposals and atomic approval receipts."""

from collections.abc import Callable
from dataclasses import asdict
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import AuthorizationError, ConflictError, NotFoundError
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal
from squid.submissions.models.revisions import SubmissionRevisionProposal

_REVISION_FACTS = TypeAdapter(RevisionFacts)


class PostgresRevisionProposals:
    """Persist review snapshots and commit the build and approval receipt together."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def create(self, proposal: RevisionProposal) -> RevisionProposal:
        session = self._session()
        await lock_uuid(session, proposal.id, namespace=AdvisoryLockNamespace.SUBMISSION_REVISION_PROPOSAL)
        existing = await session.get(SubmissionRevisionProposal, proposal.id)
        if existing is not None:
            if existing.run_id != proposal.run_id or existing.owner_account_id != proposal.owner_account_id:
                raise AuthorizationError
            return _snapshot(existing)
        values = asdict(proposal)
        values["facts"] = _REVISION_FACTS.dump_python(proposal.facts, mode="json")
        model = SubmissionRevisionProposal(**values)
        session.add(model)
        return _snapshot(model)

    async def get(self, proposal_id: UUID) -> RevisionProposal | None:
        session = self._session()
        model = await session.get(SubmissionRevisionProposal, proposal_id)
        return _snapshot(model) if model is not None else None

    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RevisionProposal, ...]:
        session = self._session()
        rows = await session.scalars(
            select(SubmissionRevisionProposal)
            .where(
                SubmissionRevisionProposal.source_message_id == source_message_id,
            )
            .order_by(SubmissionRevisionProposal.created_at.desc(), SubmissionRevisionProposal.id)
            .limit(limit)
        )
        return tuple(_snapshot(row) for row in rows)

    async def match(self, proposal: RevisionProposal) -> RevisionProposal:
        session = self._session()
        model = await session.scalar(
            select(SubmissionRevisionProposal).where(SubmissionRevisionProposal.id == proposal.id).with_for_update()
        )
        if model is None:
            raise NotFoundError
        if model.build_id is not None:
            if (model.build_id, model.expected_revision) != (proposal.build_id, proposal.expected_revision):
                message = "This candidate already has a retained review; renew it to change the target or revision."
                raise ConflictError(message)
            return _snapshot(model)
        model.build_id, model.expected_revision = proposal.build_id, proposal.expected_revision
        model.before, model.after = proposal.before, proposal.after
        return _snapshot(model)

    async def get_for_update(self, proposal_id: UUID) -> RevisionProposal | None:
        model = await self._session().scalar(
            select(SubmissionRevisionProposal).where(SubmissionRevisionProposal.id == proposal_id).with_for_update()
        )
        return _snapshot(model) if model is not None else None

    async def mark_approved(self, proposal_id: UUID, *, account_id: int, revision: int) -> RevisionProposal:
        session = self._session()
        model = await session.get(SubmissionRevisionProposal, proposal_id)
        if model is None:
            raise NotFoundError
        model.approved_by_account_id = account_id
        model.applied_revision = revision
        await session.flush()
        return _snapshot(model)


def _snapshot(model: SubmissionRevisionProposal) -> RevisionProposal:
    return RevisionProposal(
        model.id,
        model.run_id,
        model.owner_account_id,
        model.requested_by_account_id,
        model.source_message_id,
        _REVISION_FACTS.validate_python(model.facts),
        model.build_id,
        model.expected_revision,
        model.before,
        model.after,
        model.created_at,
        model.expires_at,
        model.approved_by_account_id,
        model.applied_revision,
    )
