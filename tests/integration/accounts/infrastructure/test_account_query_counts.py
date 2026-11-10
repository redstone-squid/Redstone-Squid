"""Account reads batch identities and claimants without adding a query for every returned row.

Write tests allow sub-microsecond precision loss when timestamps are reloaded.
"""

import uuid
from collections.abc import AsyncIterator
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, ClaimMethod, ClaimStatus
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.accounts.models import CreatorAlias
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.database import assert_loaded_detached, record_queries
from tests.support.unit_of_work import transact

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


def _selects(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]


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


@pytest.mark.parametrize("identity_count", [1, 3])
async def test_create_does_not_scale_with_identity_count(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
    identity_count: int,
) -> None:
    identities = (
        AccountIdentityValue.discord(1),
        AccountIdentityValue.java(uuid.UUID(int=1), username="Player"),
        AccountIdentityValue.bedrock(2**63, gamertag="Builder"),
    )[:identity_count]

    with record_queries(async_session_factory) as statements:
        await transact(repository, lambda uow: uow.accounts.create(consent=CONSENT, identities=identities), commit=True)

    assert len(_selects(statements)) == 0, _selects(statements)


async def test_get_many_costs_two_queries_regardless_of_account_count(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_ids = []
    for discord_id in range(1, 6):
        account = await transact(
            repository,
            lambda uow, discord_id=discord_id: uow.accounts.create(
                consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),)
            ),
            commit=True,
        )
        assert account.id is not None
        account_ids.append(account.id)

    with record_queries(async_session_factory) as statements:
        loaded = await transact(repository, lambda uow: uow.accounts.get_many(account_ids))

    assert set(loaded) == set(account_ids)
    assert len(_selects(statements)) == 2, _selects(statements)


async def test_get_many_of_nothing_touches_the_database_not_at_all(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with record_queries(async_session_factory) as statements:
        assert await transact(repository, lambda uow: uow.accounts.get_many([])) == {}

    assert statements == []


async def test_get_many_returns_every_identity_it_loaded(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Batching must not lose the grouping: each account keeps exactly its own identities."""
    del async_session_factory
    first = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT, identities=(AccountIdentityValue.discord(1), AccountIdentityValue.bedrock(2**62))
        ),
        commit=True,
    )
    second = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    assert first.id is not None
    assert second.id is not None

    loaded = await transact(repository, lambda uow: uow.accounts.get_many([first.id, second.id]))

    assert {identity.subject for identity in loaded[first.id].identities} == {"1", str(2**62)}
    assert {identity.subject for identity in loaded[second.id].identities} == {"2"}


async def test_pending_claims_with_claimants_does_not_scale_with_queue_length(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Presenting a claimant as more than an internal ID must not cost a query per claim."""
    async with async_session_factory.begin() as session:
        session.add_all(CreatorAlias(name=f"Name{index}") for index in range(4))

    for discord_id in range(1, 5):
        account = await transact(
            repository,
            lambda uow, discord_id=discord_id: uow.accounts.create(
                consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),)
            ),
            commit=True,
        )
        assert account.id is not None
        await transact(
            repository,
            lambda uow, discord_id=discord_id, account=account: uow.accounts.request_claim(
                name=f"Name{discord_id - 1}", account_id=account.id
            ),
            commit=True,
        )

    with record_queries(async_session_factory) as statements:
        claims = await transact(repository, lambda uow: uow.accounts.pending_claims(with_claimants=True))

    assert len(claims) == 4
    assert all(claim.claimant is not None for claim in claims)
    # The claim/alias join, then the account and identity batches.
    assert len(_selects(statements)) == 3, _selects(statements)


async def test_pending_claims_without_claimants_stays_one_query(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Name"))
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    assert account.id is not None
    await transact(repository, lambda uow: uow.accounts.request_claim(name="Name", account_id=account.id), commit=True)

    with record_queries(async_session_factory) as statements:
        claims = await transact(repository, lambda uow: uow.accounts.pending_claims())

    assert [claim.claimant for claim in claims] == [None]
    assert len(_selects(statements)) == 1, _selects(statements)


@pytest.mark.parametrize("status", [ClaimStatus.APPROVED, ClaimStatus.REJECTED])
@pytest.mark.parametrize("identity_count", [1, 3])
async def test_resolving_a_claim_names_its_claimant_at_constant_query_cost(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
    status: ClaimStatus,
    identity_count: int,
) -> None:
    identities = (
        AccountIdentityValue.discord(1),
        AccountIdentityValue.java(uuid.UUID(int=1), username="Player"),
        AccountIdentityValue.bedrock(2**63, gamertag="Builder"),
    )[:identity_count]
    claimant = await transact(
        repository, lambda uow: uow.accounts.create(consent=CONSENT, identities=identities), commit=True
    )
    assert claimant.id is not None
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="OldName"))
    claim = await transact(
        repository, lambda uow: uow.accounts.request_claim(name="OldName", account_id=claimant.id), commit=True
    )

    with record_queries(async_session_factory) as statements:
        resolved = await transact(
            repository,
            lambda uow: uow.accounts.resolve_claim(
                claim_id=claim.id, status=status, resolved_by_account_id=claimant.id
            ),
            commit=True,
        )

    assert resolved.claimant is not None
    assert len(resolved.claimant.identities) == identity_count
    # Claim, alias, claimant account, then the one batched identity load.
    assert len(_selects(statements)) == 4, _selects(statements)


async def test_create_round_trips_at_storage_precision(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reloaded timestamps preserve the microseconds supported by storage."""
    del async_session_factory
    created = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(uuid.UUID(int=1), username="Player"),
            ),
        ),
        commit=True,
    )
    assert created.id is not None

    loaded = await transact(repository, lambda uow: uow.accounts.get_by_id(created.id))
    assert loaded is not None
    assert loaded.created_at == created.created_at.round("microsecond", mode="floor")
    assert loaded.consent == created.consent
    assert [(identity.id, identity.verified_at) for identity in loaded.identities] == [
        (identity.id, identity.verified_at.round("microsecond", mode="floor") if identity.verified_at else None)
        for identity in created.identities
    ]
    assert_loaded_detached(created)
    assert_loaded_detached(loaded)
    for identity in loaded.identities:
        assert_loaded_detached(identity)


async def test_claim_timestamps_round_trip_at_storage_precision(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Claim timestamps may lose nanoseconds when reloaded from PostgreSQL."""
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Name"))
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    assert account.id is not None

    claimed = await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=account.id, name="Name", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )
    assert claimed is not None

    reloaded = await transact(repository, lambda uow: uow.accounts.get_alias_by_name("Name"))
    assert reloaded is not None
    assert claimed.claimed_at is not None
    assert reloaded.claimed_at == claimed.claimed_at.round("microsecond", mode="floor")
