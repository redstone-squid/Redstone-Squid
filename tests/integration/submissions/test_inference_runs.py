"""Inference replay preserves identity and rejects stale or conflicting invocations."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.domain import BuildCategory, BuildDraft
from squid.core.errors import AuthorizationError, ConflictError, JSONValue
from squid.submissions.infrastructure.inference_runs import PostgresInferenceRuns
from tests.support.submission_targets import seed_account_and_version


async def test_completed_run_replays_original_candidates_and_rejects_changed_input(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    repository = PostgresInferenceRuns(sessions)
    run = uuid4()
    inputs: dict[str, JSONValue] = {"model": "test", "schema_revision": 1}
    claim = await repository.begin(run, owner, inputs, capacity=1000)
    assert claim.token is not None
    candidates = await repository.complete(
        run, claim.token, (BuildDraft(category=None), BuildDraft(category=BuildCategory.DOOR, door_width=3))
    )
    replay = await repository.begin(run, owner, inputs, capacity=1000)
    assert replay.token is None
    assert replay.candidates == candidates
    assert candidates[0].facts.category is None
    assert candidates[1].facts.door_width == 3
    with pytest.raises(ConflictError):
        await repository.begin(run, owner, {"model": "different"}, capacity=1000)
    with pytest.raises(AuthorizationError):
        await repository.begin(run, owner + 1, inputs, capacity=1000)


async def test_reclaimed_run_fences_late_completion_and_failure(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    repository = PostgresInferenceRuns(sessions)
    run = uuid4()
    first = await repository.begin(run, owner, {}, capacity=1000)
    assert first.token is not None
    with pytest.raises(ConflictError):
        await repository.begin(run, owner, {}, capacity=1000)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE submission_inference_runs SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = :id"
            ),
            {"id": run},
        )
    second = await repository.begin(run, owner, {}, capacity=1000)
    assert second.token is not None
    assert second.token != first.token
    with pytest.raises(ConflictError):
        await repository.complete(run, first.token, (BuildDraft(width=1),))
    await repository.fail(run, first.token)
    result = await repository.complete(run, second.token, (BuildDraft(width=2),))
    assert result[0].facts.width == 2
