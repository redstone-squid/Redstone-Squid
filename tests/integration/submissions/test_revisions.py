"""Revision approval preserves identity and commits its receipt atomically."""

from dataclasses import replace
from functools import partial
from typing import Literal, override
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.models import Account
from squid.builds.application import BuildEditor, BuildService
from squid.builds.application.ports import BuildUnitOfWork
from squid.builds.domain import BuildCategory
from squid.builds.errors import BuildRevisionMismatchError
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError
from squid.permissions.application import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.permissions.models import PermissionGrant
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal, revision_values
from squid.submissions.application.revisions import RevisionProposalService
from squid.submissions.infrastructure.revisions import PostgresRevisionProposals
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact
from tests.support.database import record_queries
from tests.support.submission_targets import seed_account_and_version, submission_build


class EditPolicy(BuildService):
    def __init__(self) -> None:
        self.allowed = True

    @override
    async def authorize_edit_in(self, uow: BuildUnitOfWork, actor: BuildEditor, build: Build) -> None:
        if not self.allowed:
            raise AuthorizationError


async def prepared_proposal(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UnitOfWorkFactory[SqlAlchemyUnitOfWork], RevisionProposal, Build, BuildEditor, EditPolicy]:
    owner = await seed_account_and_version(sessions)
    builds = partial(SqlAlchemyUnitOfWork, sessions)
    build = submission_build(BuildCategory.DOOR, owner)
    await transact(
        builds,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    before = revision_values(build)
    facts = RevisionFacts(category=BuildCategory.DOOR, width=9)
    facts.patch(build).apply(build)
    now = Instant.now()
    proposal = RevisionProposal(
        uuid4(),
        uuid4(),
        owner,
        owner,
        123,
        facts,
        build.id,
        build.revision,
        before,
        revision_values(build),
        now,
        now.add(days=7, days_assumed_24h_ok=True),
    )
    policy = EditPolicy()
    repository = partial(SqlAlchemyUnitOfWork, sessions)
    await transact(repository, lambda uow: uow.revision_proposals.create(proposal), commit=True)
    return repository, proposal, build, BuildEditor(Actor(account_id=owner)), policy


async def test_approval_commits_once_and_preserves_build_identity(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, policy = await prepared_proposal(migrated_session_factory)
    result = await revision_service(repository, policy)._approve_prepared(proposal, build, actor)
    assert proposal.expected_revision is not None
    assert result.applied_revision == proposal.expected_revision + 1
    assert result.approved_by_account_id == actor.subject.account_id
    replay = await revision_service(repository, policy)._approve_prepared(proposal, build, actor)
    assert replay == result
    saved = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory), lambda uow: uow.builds.get_by_id(build.id or 0)
    )
    assert saved is not None
    assert saved.width == 9
    assert (saved.id, saved.category, saved.submitter_account_id, saved.submission_status) == (
        build.id,
        build.category,
        build.submitter_account_id,
        build.submission_status,
    )


async def test_stale_approval_retains_the_unapplied_proposal(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, policy = await prepared_proposal(migrated_session_factory)
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    with pytest.raises(BuildRevisionMismatchError):
        await revision_service(repository, policy)._approve_prepared(proposal, build, actor)
    retained = await transact(repository, lambda uow: uow.revision_proposals.get(proposal.id))
    assert retained is not None
    assert retained.applied_revision is None


async def test_permission_is_rechecked_inside_approval_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, policy = await prepared_proposal(migrated_session_factory)
    policy.allowed = False
    with pytest.raises(AuthorizationError):
        await revision_service(repository, policy)._approve_prepared(proposal, build, actor)
    saved = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory), lambda uow: uow.builds.get_by_id(build.id or 0)
    )
    assert saved is not None
    assert saved.width == 3


async def test_failed_receipt_rolls_back_the_build_edit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, proposal, build, actor, policy = await prepared_proposal(migrated_session_factory)
    # The receipt's account FK fails after the build write, exercising the enclosing transaction.
    original = PostgresRevisionProposals.mark_approved

    async def invalid_receipt(
        self: PostgresRevisionProposals, proposal_id: UUID, *, account_id: int, revision: int
    ) -> RevisionProposal:
        return await original(self, proposal_id, account_id=999999, revision=revision)

    monkeypatch.setattr(PostgresRevisionProposals, "mark_approved", invalid_receipt)

    with pytest.raises(IntegrityError):
        await revision_service(repository, policy)._approve_prepared(proposal, build, actor)
    saved = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory), lambda uow: uow.builds.get_by_id(build.id or 0)
    )
    assert saved is not None
    assert saved.width == 3
    retained = await transact(repository, lambda uow: uow.revision_proposals.get(proposal.id))
    assert retained is not None
    assert retained.applied_revision is None


async def test_first_target_match_is_immutable(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:

    repository, proposal, _, _, _ = await prepared_proposal(migrated_session_factory)
    with pytest.raises(ConflictError):
        await transact(
            repository,
            lambda uow: uow.revision_proposals.match(
                replace(proposal, expected_revision=(proposal.expected_revision or 0) + 1)
            ),
            commit=True,
        )


@pytest.mark.parametrize("role", ["owner", "requester", "staff"])
async def test_history_authorizes_loaded_proposals_without_per_row_queries(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    role: Literal["owner", "requester", "staff"],
) -> None:
    repository, original, _, owner, builds = await prepared_proposal(migrated_session_factory)
    async with build_edit_session(migrated_session_factory) as session:
        requester, staff = Account(), Account()
        session.add_all((requester, staff))
        await session.flush()
        session.add(PermissionGrant(pattern=BUILD_SUBMISSION_EDIT.name, effect=1, subject_account_id=staff.id))
    proposals = tuple(
        [
            await transact(
                repository,
                lambda uow, index=index: uow.revision_proposals.create(
                    replace(
                        original,
                        id=uuid4(),
                        source_message_id=456,
                        requested_by_account_id=requester.id,
                        created_at=original.created_at.add(seconds=index),
                    )
                ),
                commit=True,
            )
            for index in range(3)
        ]
    )
    actor = {
        "owner": owner.subject,
        "requester": Actor(account_id=requester.id),
        "staff": Actor(account_id=staff.id),
    }[role]
    service = RevisionProposalService(builds, PermissionService(uow_factory=repository), uow_factory=repository)

    with record_queries(migrated_session_factory) as statements:
        history = await service.list_for_source(456, actor)

    assert [row.id for row in history] == [proposal.id for proposal in reversed(proposals)]
    assert sum("FROM submission_revision_proposals" in statement for statement in statements) == 1
    assert sum("FROM permission_epoch" in statement for statement in statements) == (1 if role == "staff" else 0)
    assert await service.get(history[0].id, actor) == history[0]


async def test_history_refuses_a_list_containing_another_accounts_proposal(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, _, owner, builds = await prepared_proposal(migrated_session_factory)
    async with build_edit_session(migrated_session_factory) as session:
        other = Account()
        session.add(other)
        await session.flush()
    foreign = await transact(
        repository,
        lambda uow: uow.revision_proposals.create(
            replace(proposal, id=uuid4(), owner_account_id=other.id, requested_by_account_id=other.id)
        ),
        commit=True,
    )
    service = RevisionProposalService(builds, PermissionService(uow_factory=repository), uow_factory=repository)

    assert (await service.get(proposal.id, owner.subject)).id == proposal.id
    with pytest.raises(AuthorizationError):
        await service.get(foreign.id, owner.subject)
    with pytest.raises(AuthorizationError):
        await service.list_for_source(proposal.source_message_id, owner.subject)
    assert await service.list_for_source(999, owner.subject) == ()


def revision_service(factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork], builds: BuildService) -> RevisionProposalService:
    return RevisionProposalService(builds, PermissionService(uow_factory=factory), uow_factory=factory)
