"""Query-count coverage for the account read and write paths.

The domain objects are frozen dataclasses that must not learn about SQLAlchemy, so identities are
mapped explicitly rather than through a relationship. That is deliberate, but it made the *fetch*
per row too: one identity query per account, and one refresh per identity on insert. These tests
pin the fixed cost so it cannot regress into an N+1 again.
"""

import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, ClaimMethod
from squid.accounts.domain import AccountIdentity as AccountIdentityValue
from squid.accounts.infrastructure.models import CreatorAlias
from squid.accounts.infrastructure.repository import AccountRepository
from squid.persistence.base import Base

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    Base.metadata.tables["public_creator_redirects"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
]


@contextmanager
def _counting(session_factory: async_sessionmaker[AsyncSession]) -> Generator[list[str]]:
    """Record every statement executed on the factory's engine."""
    statements: list[str] = []
    engine = session_factory.kw["bind"].sync_engine

    def before_cursor_execute(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def _selects(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]


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


@pytest.mark.parametrize("identity_count", [1, 3])
async def test_create_does_not_scale_with_identity_count(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
    identity_count: int,
) -> None:
    """`create()` used to refresh each inserted identity row individually."""
    identities = (
        AccountIdentityValue.discord(1),
        AccountIdentityValue.java(uuid.UUID(int=1), username="Player"),
        AccountIdentityValue.bedrock(2**63, gamertag="Builder"),
    )[:identity_count]

    with _counting(async_session_factory) as statements:
        await repository.create(consent=CONSENT, identities=identities)

    assert len(_selects(statements)) == 0, _selects(statements)


async def test_get_many_costs_two_queries_regardless_of_account_count(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_ids = []
    for discord_id in range(1, 6):
        account = await repository.create(
            consent=CONSENT,
            identities=(AccountIdentityValue.discord(discord_id),),
        )
        assert account.id is not None
        account_ids.append(account.id)

    with _counting(async_session_factory) as statements:
        loaded = await repository.get_many(account_ids)

    assert set(loaded) == set(account_ids)
    assert len(_selects(statements)) == 2, _selects(statements)


async def test_get_many_of_nothing_touches_the_database_not_at_all(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with _counting(async_session_factory) as statements:
        assert await repository.get_many([]) == {}

    assert statements == []


async def test_get_many_returns_every_identity_it_loaded(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Batching must not lose the grouping: each account keeps exactly its own identities."""
    del async_session_factory
    first = await repository.create(
        consent=CONSENT,
        identities=(AccountIdentityValue.discord(1), AccountIdentityValue.bedrock(2**62)),
    )
    second = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
    assert first.id is not None
    assert second.id is not None

    loaded = await repository.get_many([first.id, second.id])

    assert {identity.subject for identity in loaded[first.id].identities} == {"1", str(2**62)}
    assert {identity.subject for identity in loaded[second.id].identities} == {"2"}


async def test_pending_claims_with_claimants_does_not_scale_with_queue_length(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Presenting a claimant as more than an internal ID must not cost a query per claim."""
    async with async_session_factory.begin() as session:
        session.add_all(CreatorAlias(name=f"Name{index}") for index in range(4))

    for discord_id in range(1, 5):
        account = await repository.create(
            consent=CONSENT,
            identities=(AccountIdentityValue.discord(discord_id),),
        )
        assert account.id is not None
        await repository.request_claim(name=f"Name{discord_id - 1}", account_id=account.id)

    with _counting(async_session_factory) as statements:
        claims = await repository.pending_claims(with_claimants=True)

    assert len(claims) == 4
    assert all(claim.claimant is not None for claim in claims)
    # The claim/alias join, then the account and identity batches.
    assert len(_selects(statements)) == 3, _selects(statements)


async def test_pending_claims_without_claimants_stays_one_query(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Name"))
    account = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),))
    assert account.id is not None
    await repository.request_claim(name="Name", account_id=account.id)

    with _counting(async_session_factory) as statements:
        claims = await repository.pending_claims()

    assert [claim.claimant for claim in claims] == [None]
    assert len(_selects(statements)) == 1, _selects(statements)


async def test_create_returns_exactly_what_it_persisted(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Dropping the per-row refresh must not let the returned object drift from the row.

    `Instant.now()` carries nanoseconds and `timestamptz` carries microseconds, so the
    refresh was silently supplying the floored value the database kept.
    """
    del async_session_factory
    created = await repository.create(
        consent=CONSENT,
        identities=(AccountIdentityValue.discord(1), AccountIdentityValue.java(uuid.UUID(int=1), username="Player")),
    )
    assert created.id is not None

    assert await repository.get_by_id(created.id) == created


async def test_claim_timestamps_survive_a_reload_unchanged(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same invariant as above, for every timestamp this repository writes.

    `_now()` floors to microseconds because `timestamptz` does; without it a claim returned
    from the write path carries nanoseconds the row does not have.
    """
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Name"))
    account = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),))
    assert account.id is not None

    claimed = await repository.claim_unclaimed_alias(
        account_id=account.id,
        name="Name",
        method=ClaimMethod.VERIFIED_IGN,
    )
    assert claimed is not None

    reloaded = await repository.get_alias_by_name("Name")
    assert reloaded is not None
    assert reloaded.claimed_at == claimed.claimed_at
