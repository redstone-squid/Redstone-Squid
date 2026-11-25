"""Record recomputation leases protect each claimed item with a token.

Re-enqueueing clears the old claim so new work can be leased without allowing an earlier
worker to acknowledge it. Expired claims become available again. A kind is leased to one
worker at a time."""

from functools import partial
from typing import Any

import pytest
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.builds.domain import BuildCategory
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.domain import BuildKind
from tests.support.build_edits import transact_build_edit
from tests.support.submission_targets import seed_account_and_version, submission_build
from tests.support.unit_of_work import transact


@pytest.fixture(autouse=True)
async def empty_queue(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Remove the door and extender rebuilds seeded by migrations so claims cover only test data."""
    async with migrated_session_factory.begin() as session:
        await session.execute(text("DELETE FROM record_recompute_queue"))


async def _enqueue(session_factory: async_sessionmaker[AsyncSession], scope_key: str, kind: str = "door") -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO record_recompute_queue (scope_key, build_kind, target, reasons) "
                "VALUES (:scope, :kind, :target, '[\"source_change\"]'::jsonb) "
                "ON CONFLICT (scope_key) DO UPDATE "
                "SET enqueued_at = now(), available_at = now(), claimed_at = NULL, claim_token = NULL"
            ),
            {"scope": scope_key, "kind": kind, "target": "backstop" if scope_key.endswith(":backstop") else "kind"},
        )


async def _count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return (await session.execute(text("SELECT count(*) FROM record_recompute_queue"))).scalar_one()


async def test_a_scope_lease_cannot_acknowledge_work_enqueued_during_the_run(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A replacement claim must survive acknowledgement by the worker that held the earlier token."""
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    worker_a = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    assert worker_a.kind is BuildKind.DOOR

    # New DOOR work arrives mid-run and clears the claim, exactly as the trigger does.
    await _enqueue(migrated_session_factory, "door")
    worker_b = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    assert worker_b.kind is BuildKind.DOOR
    assert worker_b.claim_tokens != worker_a.claim_tokens

    await transact(repository, lambda uow: uow.records.complete_recompute(worker_a), commit=True)

    assert await _count(migrated_session_factory) == 1
    async with migrated_session_factory() as session:
        still_leased = (await session.execute(text("SELECT claim_token FROM record_recompute_queue"))).scalar_one()
    assert still_leased == worker_b.claim_tokens[0]


async def test_a_crashed_workers_lease_is_reclaimed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    abandoned = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE record_recompute_queue SET claimed_at = now() - interval '6 minutes'"))

    reclaimed = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    assert reclaimed.kind is BuildKind.DOOR
    assert reclaimed.claim_tokens != abandoned.claim_tokens


async def test_a_failed_lease_backs_off_and_stays_queued(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recomputation has no retry ceiling, so failures remain queued after backoff."""
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    lease = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)

    await transact(repository, lambda uow: uow.records.fail_recompute(lease, "boom"), commit=True)

    row = await _one(migrated_session_factory)
    assert row == (1, None, None, True, "boom")
    # Backed off, not dropped, and not claimable until the delay elapses.
    assert (await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)).kind is None
    assert await _count(migrated_session_factory) == 1


async def test_a_kind_is_leased_to_one_worker_at_a_time(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    first = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    await _enqueue(migrated_session_factory, "door:backstop")
    await _enqueue(migrated_session_factory, "extender", kind="extender")

    # The door item waits for the first worker; the extender item does not.
    second = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    third = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    assert (first.kind, second.kind, third.kind) == (BuildKind.DOOR, BuildKind.EXTENDER, None)

    await transact(repository, lambda uow: uow.records.complete_recompute(first), commit=True)
    fourth = await transact(repository, lambda uow: uow.records.claim_recompute(limit=10), commit=True)
    assert fourth.kind is BuildKind.DOOR


async def test_build_changes_queue_one_item_per_build(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build's lease carries the build, so the worker recomputes only what it affects."""
    owner = await seed_account_and_version(migrated_session_factory)
    builds = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    ids: list[int] = []
    for _ in range(2):
        build = submission_build(BuildCategory.DOOR, owner)
        await transact_build_edit(builds, lambda uow, build=build: uow.builds.save(build), commit=True)
        assert build.id is not None
        ids.append(build.id)

    lease = await transact(builds, lambda uow: uow.records.claim_recompute(limit=10), commit=True)

    assert lease.kind is BuildKind.DOOR
    assert lease.builds == frozenset(ids)
    assert not lease.full


async def _one(session_factory: async_sessionmaker[AsyncSession]) -> Row[Any]:
    async with session_factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT attempts, claimed_at, claim_token, available_at > now(), last_error "
                    "FROM record_recompute_queue"
                )
            )
        ).one()
