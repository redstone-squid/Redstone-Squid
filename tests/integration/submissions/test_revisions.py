"""Revision approval preserves identity and commits its receipt atomically."""

from dataclasses import replace
from typing import override
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.application import BuildEditor, BuildService
from squid.builds.domain import Build, BuildCategory
from squid.builds.errors import BuildRevisionMismatchError
from squid.builds.infrastructure.repository import BuildRepository
from squid.core.errors import AuthorizationError
from squid.permissions.domain import Subject
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal, revision_values
from squid.submissions.infrastructure.revisions import PostgresRevisionProposals
from tests.support.submission_targets import seed_account_and_version, submission_build


class EditPolicy(BuildService):
    def __init__(self) -> None:
        self.allowed = True

    @override
    async def authorize_edit(self, actor: BuildEditor, build: Build) -> None:
        if not self.allowed:
            raise AuthorizationError


async def prepared_proposal(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[PostgresRevisionProposals, RevisionProposal, Build, BuildEditor, EditPolicy]:
    owner = await seed_account_and_version(sessions)
    builds = BuildRepository(sessions)
    build = submission_build(BuildCategory.DOOR, owner)
    await builds.save(build)
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
    repository = PostgresRevisionProposals(sessions, builds, policy)
    await repository.create(proposal)
    return repository, proposal, build, BuildEditor(Subject(account_id=owner)), policy


async def test_approval_commits_once_and_preserves_build_identity(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, _ = await prepared_proposal(migrated_session_factory)
    result = await repository.approve(proposal, build, actor)
    assert proposal.expected_revision is not None
    assert result.applied_revision == proposal.expected_revision + 1
    assert result.approved_by_account_id == actor.subject.account_id
    replay = await repository.approve(proposal, build, actor)
    assert replay == result
    saved = await BuildRepository(migrated_session_factory).get_by_id(build.id or 0)
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
    repository, proposal, build, actor, _ = await prepared_proposal(migrated_session_factory)
    await BuildRepository(migrated_session_factory).save(build)
    with pytest.raises(BuildRevisionMismatchError):
        await repository.approve(proposal, build, actor)
    retained = await repository.get(proposal.id)
    assert retained is not None
    assert retained.applied_revision is None


async def test_permission_is_rechecked_inside_approval_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, policy = await prepared_proposal(migrated_session_factory)
    policy.allowed = False
    with pytest.raises(AuthorizationError):
        await repository.approve(proposal, build, actor)
    saved = await BuildRepository(migrated_session_factory).get_by_id(build.id or 0)
    assert saved is not None
    assert saved.width == 3


async def test_failed_receipt_rolls_back_the_build_edit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository, proposal, build, actor, _ = await prepared_proposal(migrated_session_factory)
    # The receipt's account FK fails after the build write, exercising the enclosing transaction.
    invalid_actor = BuildEditor(Subject(account_id=999999))
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await repository.approve(proposal, build, invalid_actor)
    saved = await BuildRepository(migrated_session_factory).get_by_id(build.id or 0)
    assert saved is not None
    assert saved.width == 3
    retained = await repository.get(proposal.id)
    assert retained is not None
    assert retained.applied_revision is None


async def test_first_target_match_is_immutable(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    from squid.core.errors import ConflictError

    repository, proposal, _, _, _ = await prepared_proposal(migrated_session_factory)
    with pytest.raises(ConflictError):
        await repository.match(replace(proposal, expected_revision=(proposal.expected_revision or 0) + 1))
