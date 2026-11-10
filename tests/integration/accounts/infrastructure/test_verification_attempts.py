"""Verification failures increment atomically so concurrent guesses cannot bypass the lockout threshold."""

import asyncio
from collections.abc import AsyncIterator
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant, patch_current_time

from squid.accounts.domain import IdentityProvider
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

_TABLES = [Base.metadata.tables["verification_attempts"]]
DISCORD = IdentityProvider.DISCORD
SUBJECT = "123456789"
MAX_FAILURES = 5
LOCKOUT = 900


@pytest.fixture
async def repository(
    async_engine: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[UnitOfWorkFactory[SqlAlchemyUnitOfWork]]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _fail(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], subject: str = SUBJECT) -> Instant | None:
    return await transact(
        repository,
        lambda uow: uow.accounts.record_verification_failure(
            DISCORD, subject, max_failures=MAX_FAILURES, lockout_seconds=LOCKOUT
        ),
        commit=True,
    )


async def test_no_lockout_before_the_cap(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None
        assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT)) is None


async def test_the_cap_starts_a_lockout_and_the_read_agrees(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)

    proposed_at = Instant.now().round("second", mode="floor").add(nanoseconds=123456789)
    with patch_current_time(proposed_at, keep_ticking=False):
        locked_until = await _fail(repository)

    assert locked_until is not None
    observed = await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT))
    assert observed == locked_until
    assert locked_until == proposed_at.add(seconds=LOCKOUT).round("microsecond", mode="floor")
    assert locked_until > Instant.now()


async def test_a_success_clears_the_row(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)

    await transact(repository, lambda uow: uow.accounts.clear_verification_failures(DISCORD, SUBJECT), commit=True)

    # Clearing failures restores the whole budget, rather than just hiding the current lockout.
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None


async def test_reaching_the_cap_resets_the_count(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """Explicitly clearing a locked identity restores its failure budget."""
    for _ in range(MAX_FAILURES):
        await _fail(repository)

    await transact(repository, lambda uow: uow.accounts.clear_verification_failures(DISCORD, SUBJECT), commit=True)
    assert await _fail(repository) is None


async def test_the_lockout_is_scoped_to_one_identity(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    for _ in range(MAX_FAILURES):
        await _fail(repository)

    assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, "987654321")) is None
    assert (
        await transact(repository, lambda uow: uow.accounts.verification_lockout(IdentityProvider.JAVA, SUBJECT))
        is None
    )


async def test_an_expired_lockout_stops_blocking(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """A negative TTL creates an expired lockout without waiting for the production fifteen-minute interval."""
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)
    await transact(
        repository,
        lambda uow: uow.accounts.record_verification_failure(
            DISCORD, SUBJECT, max_failures=MAX_FAILURES, lockout_seconds=-1
        ),
        commit=True,
    )

    assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT)) is None
    # And the budget is spendable again, so an elapsed lockout leaves no residue.
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None


async def test_concurrent_failures_are_all_counted(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """The upsert increments in one statement, so no attempt is lost to a lost update."""
    results = await asyncio.gather(*(_fail(repository) for _ in range(MAX_FAILURES)))

    # Exactly one of the racing attempts is the one that crossed the cap.
    assert sum(1 for result in results if result is not None) == 1
    assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT)) is not None


async def test_failing_again_while_locked_does_not_report_a_new_lockout(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    """Only the call that starts a lockout returns its deadline; later failures must not extend it."""
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)
    started = await _fail(repository)
    assert started is not None

    assert await _fail(repository) is None
    # The running lockout is untouched rather than extended by the extra attempt.
    assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT)) == started


async def test_a_cap_of_one_locks_on_the_first_failure(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """The insert path has to honour the cap too, not just the conflict path."""
    locked_until = await transact(
        repository,
        lambda uow: uow.accounts.record_verification_failure(DISCORD, SUBJECT, max_failures=1, lockout_seconds=LOCKOUT),
        commit=True,
    )

    assert locked_until is not None
    assert await transact(repository, lambda uow: uow.accounts.verification_lockout(DISCORD, SUBJECT)) == locked_until
