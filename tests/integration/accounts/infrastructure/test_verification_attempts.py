"""The verification-code attempt cap, against real Postgres.

The counter has to hold under concurrency, because the whole point is to bound guessing: a
read-modify-write would let parallel attempts each read the same count and overwrite one another,
which makes the cap evadable by simply not waiting for a reply.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.repository import AccountRepository
from squid.persistence.base import Base

_TABLES = [Base.metadata.tables["verification_attempts"]]
DISCORD = IdentityProvider.DISCORD
SUBJECT = "123456789"
MAX_FAILURES = 5
LOCKOUT = 900


@pytest.fixture
async def repository(
    async_engine: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AccountRepository]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield AccountRepository(async_session_factory, "pepper")
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _fail(repository: AccountRepository, subject: str = SUBJECT) -> Instant | None:
    return await repository.record_verification_failure(
        DISCORD, subject, max_failures=MAX_FAILURES, lockout_seconds=LOCKOUT
    )


async def test_no_lockout_before_the_cap(repository: AccountRepository) -> None:
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None
        assert await repository.verification_lockout(DISCORD, SUBJECT) is None


async def test_the_cap_starts_a_lockout_and_the_read_agrees(repository: AccountRepository) -> None:
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)

    locked_until = await _fail(repository)

    assert locked_until is not None
    observed = await repository.verification_lockout(DISCORD, SUBJECT)
    assert observed == locked_until
    assert locked_until > Instant.now()


async def test_a_success_clears_the_row(repository: AccountRepository) -> None:
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)

    await repository.clear_verification_failures(DISCORD, SUBJECT)

    # Not merely "not locked": the budget must be whole again, so the next cap-1 failures are free.
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None


async def test_reaching_the_cap_resets_the_count(repository: AccountRepository) -> None:
    """Otherwise the first failure after a lockout expires would re-lock immediately."""
    for _ in range(MAX_FAILURES):
        await _fail(repository)

    await repository.clear_verification_failures(DISCORD, SUBJECT)
    assert await _fail(repository) is None


async def test_the_lockout_is_scoped_to_one_identity(repository: AccountRepository) -> None:
    for _ in range(MAX_FAILURES):
        await _fail(repository)

    assert await repository.verification_lockout(DISCORD, "987654321") is None
    assert await repository.verification_lockout(IdentityProvider.JAVA, SUBJECT) is None


async def test_an_expired_lockout_stops_blocking(repository: AccountRepository) -> None:
    """No sweeper reclaims a finished lockout; the read simply stops honouring it.

    A negative `lockout_seconds` is only a device for fabricating an already-elapsed lockout without
    waiting fifteen minutes -- production always passes `VERIFICATION_LOCKOUT_SECONDS`.
    """
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)
    await repository.record_verification_failure(DISCORD, SUBJECT, max_failures=MAX_FAILURES, lockout_seconds=-1)

    assert await repository.verification_lockout(DISCORD, SUBJECT) is None
    # And the budget is spendable again, so an elapsed lockout leaves no residue.
    for _ in range(MAX_FAILURES - 1):
        assert await _fail(repository) is None


async def test_concurrent_failures_are_all_counted(repository: AccountRepository) -> None:
    """The upsert increments in one statement, so no attempt is lost to a lost update."""
    results = await asyncio.gather(*(_fail(repository) for _ in range(MAX_FAILURES)))

    # Exactly one of the racing attempts is the one that crossed the cap.
    assert sum(1 for result in results if result is not None) == 1
    assert await repository.verification_lockout(DISCORD, SUBJECT) is not None


async def test_failing_again_while_locked_does_not_report_a_new_lockout(repository: AccountRepository) -> None:
    """The return value means "this call started a lockout", so it must not repeat for a running one.

    Unreachable through the service, which refuses before redeeming, but the log line that fires on a
    non-`None` return would otherwise claim a lockout began every time an already-locked identity
    tried again.
    """
    for _ in range(MAX_FAILURES - 1):
        await _fail(repository)
    started = await _fail(repository)
    assert started is not None

    assert await _fail(repository) is None
    # The running lockout is untouched rather than extended by the extra attempt.
    assert await repository.verification_lockout(DISCORD, SUBJECT) == started


async def test_a_cap_of_one_locks_on_the_first_failure(repository: AccountRepository) -> None:
    """The insert path has to honour the cap too, not just the conflict path."""
    locked_until = await repository.record_verification_failure(
        DISCORD, SUBJECT, max_failures=1, lockout_seconds=LOCKOUT
    )

    assert locked_until is not None
    assert await repository.verification_lockout(DISCORD, SUBJECT) == locked_until
