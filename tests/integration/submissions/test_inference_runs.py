"""Inference replay preserves identity and rejects stale or conflicting invocations."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.domain import BuildCategory, BuildDraft
from squid.core.errors import AuthorizationError, ConflictError, JSONValue
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.support.submission_targets import seed_account_and_version
from tests.support.unit_of_work import transact


async def test_completed_run_replays_original_candidates_and_rejects_changed_input(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    repository = partial(SqlAlchemyUnitOfWork, sessions)
    run = uuid4()
    inputs: dict[str, JSONValue] = {"model": "test", "schema_revision": 1}
    claim = await transact(
        repository, lambda uow: uow.inference_runs.begin(run, owner, inputs, capacity=1000), commit=True
    )
    assert claim.token is not None
    candidates = await transact(
        repository,
        lambda uow: uow.inference_runs.complete(
            run, claim.token, (BuildDraft(category=None), BuildDraft(category=BuildCategory.DOOR, door_width=3))
        ),
        commit=True,
    )
    replay = await transact(
        repository, lambda uow: uow.inference_runs.begin(run, owner, inputs, capacity=1000), commit=True
    )
    assert replay.token is None
    assert replay.candidates == candidates
    assert candidates[0].facts.category is None
    assert candidates[1].facts.door_width == 3
    with pytest.raises(ConflictError):
        await transact(
            repository,
            lambda uow: uow.inference_runs.begin(run, owner, {"model": "different"}, capacity=1000),
            commit=True,
        )
    with pytest.raises(AuthorizationError):
        await transact(
            repository, lambda uow: uow.inference_runs.begin(run, owner + 1, inputs, capacity=1000), commit=True
        )


async def test_reclaimed_run_fences_late_completion_and_failure(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    repository = partial(SqlAlchemyUnitOfWork, sessions)
    run = uuid4()
    first = await transact(repository, lambda uow: uow.inference_runs.begin(run, owner, {}, capacity=1000), commit=True)
    assert first.token is not None
    with pytest.raises(ConflictError):
        await transact(repository, lambda uow: uow.inference_runs.begin(run, owner, {}, capacity=1000), commit=True)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE submission_inference_runs SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = :id"
            ),
            {"id": run},
        )
    second = await transact(
        repository, lambda uow: uow.inference_runs.begin(run, owner, {}, capacity=1000), commit=True
    )
    assert second.token is not None
    assert second.token != first.token
    with pytest.raises(ConflictError):
        await transact(
            repository, lambda uow: uow.inference_runs.complete(run, first.token, (BuildDraft(width=1),)), commit=True
        )
    await transact(repository, lambda uow: uow.inference_runs.fail(run, first.token), commit=True)
    result = await transact(
        repository, lambda uow: uow.inference_runs.complete(run, second.token, (BuildDraft(width=2),)), commit=True
    )
    assert result[0].facts.width == 2


async def test_completed_empty_run_releases_admission_capacity(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from squid.submissions.errors import DraftCapacityExceededError

    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    repository = partial(SqlAlchemyUnitOfWork, sessions)
    run = uuid4()
    claim = await transact(
        repository, lambda uow: uow.inference_runs.begin(run, owner, {"purpose": "submission"}, capacity=1), commit=True
    )
    assert claim.token is not None
    with pytest.raises(DraftCapacityExceededError):
        await transact(
            repository,
            lambda uow: uow.inference_runs.begin(uuid4(), owner, {"purpose": "submission"}, capacity=1),
            commit=True,
        )
    await transact(repository, lambda uow: uow.inference_runs.complete(run, claim.token, ()), commit=True)
    next_claim = await transact(
        repository,
        lambda uow: uow.inference_runs.begin(uuid4(), owner, {"purpose": "submission"}, capacity=1),
        commit=True,
    )
    assert next_claim.token is not None
