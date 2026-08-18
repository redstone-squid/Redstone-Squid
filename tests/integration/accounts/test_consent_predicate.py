"""The Python and SQL spellings of the consent gate must answer identically."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, consent_refresh_required
from squid.accounts.infrastructure.consent import account_consent_current
from squid.accounts.infrastructure.models import Account as AccountModel
from squid.persistence.base import Base

SUPERSEDED = "1970-01-01"
BEFORE_CUTOFF = Instant.from_utc(2026, 8, 3)
AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)

CASES = [
    pytest.param(BEFORE_CUTOFF, None, id="grandfathered"),
    pytest.param(AFTER_CUTOFF, None, id="never-consented"),
    pytest.param(BEFORE_CUTOFF, CURRENT_CONSENT_VERSION, id="old-account-current-receipt"),
    pytest.param(AFTER_CUTOFF, CURRENT_CONSENT_VERSION, id="current-receipt"),
    pytest.param(BEFORE_CUTOFF, SUPERSEDED, id="old-account-stale-receipt"),
    pytest.param(AFTER_CUTOFF, SUPERSEDED, id="stale-receipt"),
]

_TABLES = [Base.metadata.tables["accounts"]]


@pytest.fixture
async def account_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.mark.parametrize(("created_at", "consent_version"), CASES)
async def test_both_spellings_of_the_consent_gate_agree(
    account_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
    created_at: Instant,
    consent_version: str | None,
) -> None:
    """Drift between these two is invisible until a caller is refused on one transport only.

    That is exactly what happened before they were unified: a pre-cutoff account was
    grandfathered in a browser and refused on the CLI, because the SQL had no cutoff.
    """
    del account_tables
    async with async_session_factory.begin() as session:
        account = AccountModel(
            created_at=created_at,
            consent_version=consent_version,
            consented_at=None if consent_version is None else created_at,
        )
        session.add(account)
        await session.flush()
        account_id = account.id

    async with async_session_factory() as session:
        writable = await session.scalar(
            select(AccountModel.id).where(AccountModel.id == account_id, account_consent_current())
        )

    sql_says_refresh_needed = writable is None
    assert sql_says_refresh_needed == consent_refresh_required(created_at, consent_version)
