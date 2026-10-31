"""PostgreSQL revision proposals and atomic approval receipts."""

from dataclasses import asdict
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.application import BuildEditor, BuildService
from squid.builds.domain import Build
from squid.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.repository import BuildRepository
from squid.core.errors import AuthorizationError, ConflictError, NotFoundError
from squid.persistence.advisory_locks import AdvisoryLockNamespace, lock_uuid
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal
from squid.submissions.infrastructure.revision_models import SubmissionRevisionProposal

_REVISION_FACTS = TypeAdapter(RevisionFacts)


class PostgresRevisionProposals:
    """Persist review snapshots and commit the build and approval receipt together."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], repository: BuildRepository, builds: BuildService
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._builds = builds

    async def create(self, proposal: RevisionProposal) -> RevisionProposal:
        async with self._sessions.begin() as session:
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
        async with self._sessions() as session:
            model = await session.get(SubmissionRevisionProposal, proposal_id)
            return _snapshot(model) if model is not None else None

    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RevisionProposal, ...]:
        async with self._sessions() as session:
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
        async with self._sessions.begin() as session:
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

    async def approve(self, proposal: RevisionProposal, build: Build, actor: BuildEditor) -> RevisionProposal:
        async with self._sessions.begin() as session:
            # Account merging takes build rows before submission references; keep that order.
            current = await session.scalar(
                select(SQLBuild).where(SQLBuild.id == proposal.build_id).with_for_update(of=SQLBuild.id)
            )
            if current is None:
                raise BuildNotFoundError(proposal.build_id or 0)
            model = await session.scalar(
                select(SubmissionRevisionProposal).where(SubmissionRevisionProposal.id == proposal.id).with_for_update()
            )
            if model is None:
                raise NotFoundError
            if model.applied_revision is not None:
                return _snapshot(model)
            if model.expires_at <= Instant.now() or (model.build_id, model.expected_revision) != (
                proposal.build_id,
                proposal.expected_revision,
            ):
                message = "The proposal is no longer the reviewed revision."
                raise ConflictError(message)
            if current.revision != proposal.expected_revision:
                raise BuildRevisionMismatchError(
                    current.id, expected_revision=proposal.expected_revision, current_revision=current.revision
                )
            if (current.submitter_account_id, current.category, current.submission_status) != (
                build.submitter_account_id,
                build.category,
                build.submission_status,
            ) or model.owner_account_id != build.submitter_account_id:
                raise AuthorizationError
            await self._builds.authorize_edit(actor, build)
            build.edited_time = Instant.now()
            await self._repository.update_in_session(session, build)
            model.approved_by_account_id = actor.subject.account_id
            model.applied_revision = build.revision
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
