"""PostgreSQL storage for recalculation proposals and their approval receipts.

Rows live in `submission_revision_proposals`, the table's name from before the rename.
"""

from collections.abc import Callable
from dataclasses import asdict
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import AuthorizationError, ConflictError, NotFoundError
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.submissions.application.recalculation_values import RecalculationFacts, RecalculationProposal
from squid.submissions.models.recalculation import SubmissionRecalculationProposal

_REVISION_FACTS = TypeAdapter(RecalculationFacts)


class PostgresRecalculationProposals:
    """Store proposal snapshots and approval receipts in the caller's transaction.

    The application service saves the build in that same transaction. Repository
    methods neither authorize the actor nor commit on their own.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def create(self, proposal: RecalculationProposal) -> RecalculationProposal:
        session = self._session()
        await lock_uuid(session, proposal.id, namespace=AdvisoryLockNamespace.SUBMISSION_RECALCULATION_PROPOSAL)
        existing = await session.get(SubmissionRecalculationProposal, proposal.id)
        if existing is not None:
            if existing.run_id != proposal.run_id or existing.owner_account_id != proposal.owner_account_id:
                raise AuthorizationError
            return _snapshot(existing)
        values = asdict(proposal)
        values["facts"] = _REVISION_FACTS.dump_python(proposal.facts, mode="json")
        model = SubmissionRecalculationProposal(**values)
        session.add(model)
        return _snapshot(model)

    async def get(self, proposal_id: UUID) -> RecalculationProposal | None:
        session = self._session()
        model = await session.get(SubmissionRecalculationProposal, proposal_id)
        return _snapshot(model) if model is not None else None

    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RecalculationProposal, ...]:
        session = self._session()
        rows = await session.scalars(
            select(SubmissionRecalculationProposal)
            .where(
                SubmissionRecalculationProposal.source_message_id == source_message_id,
            )
            .order_by(SubmissionRecalculationProposal.created_at.desc(), SubmissionRecalculationProposal.id)
            .limit(limit)
        )
        return tuple(_snapshot(row) for row in rows)

    async def match(self, proposal: RecalculationProposal) -> RecalculationProposal:
        session = self._session()
        model = await session.scalar(
            select(SubmissionRecalculationProposal)
            .where(SubmissionRecalculationProposal.id == proposal.id)
            .with_for_update()
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

    async def get_for_update(self, proposal_id: UUID) -> RecalculationProposal | None:
        model = await self._session().scalar(
            select(SubmissionRecalculationProposal)
            .where(SubmissionRecalculationProposal.id == proposal_id)
            .with_for_update()
        )
        return _snapshot(model) if model is not None else None

    async def mark_approved(self, proposal_id: UUID, *, account_id: int, revision: int) -> RecalculationProposal:
        session = self._session()
        model = await session.get(SubmissionRecalculationProposal, proposal_id)
        if model is None:
            raise NotFoundError
        model.approved_by_account_id = account_id
        model.applied_revision = revision
        await session.flush()
        return _snapshot(model)


def _snapshot(model: SubmissionRecalculationProposal) -> RecalculationProposal:
    return RecalculationProposal(
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
