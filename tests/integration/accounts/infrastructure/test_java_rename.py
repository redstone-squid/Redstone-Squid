"""Java identity refresh and linking share creator-name reconciliation.

The cases cover unchanged names, new or unclaimed aliases, retained earlier credits, and
contested aliases that require a staff decision before ownership can move.
"""

from collections.abc import AsyncGenerator
from functools import partial
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import (
    PRIVACY_NOTICE_VERSION,
    IdentityProvider,
    PrivacyNoticeReceipt,
)
from squid.core.accounts.errors import MinecraftAccountNotFoundError
from squid.core.accounts.models import AccountIdentity, CreatorAlias
from squid.core.accounts.models import AccountIdentity as AccountIdentityValue
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

CONSENT = PrivacyNoticeReceipt(PRIVACY_NOTICE_VERSION, Instant.from_utc(2026, 8, 4))
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    Base.metadata.tables["public_account_redirects"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
]


@pytest.fixture
async def account_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.fixture
def repository(
    account_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    del account_tables
    return partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")


async def _linked_account_id(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], discord_id: int = 1, *, username: str = "OldName"
) -> int:
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(discord_id),
                AccountIdentityValue.java(JAVA_UUID, username=username),
            ),
        ),
        commit=True,
    )
    assert account.id is not None
    return account.id


async def _add_alias(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with session_factory.begin() as session:
        alias = CreatorAlias(name=name)
        session.add(alias)
        await session.flush()
        return alias.id


async def test_unchanged_name_reports_no_rename(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    account_id = await _linked_account_id(repository, username="Steve")

    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="Steve"),
        commit=True,
    )

    assert refresh.renamed is False
    assert refresh.previous_name == "Steve"
    assert refresh.current_name == "Steve"
    assert refresh.is_contested is False


@pytest.mark.parametrize("already_credited", [True, False])
async def test_rename_does_not_claim_or_publish_a_name(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
    already_credited: bool,
) -> None:
    account_id = await _linked_account_id(repository, username="OldName")
    if already_credited:
        await _add_alias(async_session_factory, "NewName")
    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )
    assert refresh.renamed
    assert refresh.claimed_alias is None
    assert refresh.opened_claim is None
    async with async_session_factory() as session:
        alias = await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "NewName"))
        assert (alias is not None) == already_credited
        assert alias is None or alias.account_id is None


async def test_refresh_is_idempotent(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    account_id = await _linked_account_id(repository, username="OldName")

    first = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )
    second = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )

    assert first.claimed_alias is None
    assert second.claimed_alias is None
    assert second.renamed is False, "the second refresh sees the name it just stored"


async def test_refresh_stores_the_new_display_name(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _linked_account_id(repository, username="OldName")

    await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )

    async with async_session_factory() as session:
        identity = await session.scalar(
            select(AccountIdentity).where(
                AccountIdentity.account_id == account_id,
                AccountIdentity.provider == IdentityProvider.JAVA,
            )
        )
        assert identity is not None
        assert identity.display_name == "NewName"


async def test_refresh_of_an_unlinked_uuid_is_rejected(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    unlinked = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    assert unlinked.id is not None

    with pytest.raises(MinecraftAccountNotFoundError):
        await transact(
            repository,
            lambda uow: uow.accounts.refresh_java_identity(
                account_id=unlinked.id, java_uuid=JAVA_UUID, username="Whoever"
            ),
            commit=True,
        )


async def test_linking_reconciles_the_same_way_a_refresh_does(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Linking a Java identity also creates and claims its available creator alias."""
    await transact(
        repository,
        lambda uow: uow.accounts.replace_verification_code(
            minecraft_uuid=JAVA_UUID, code="123456", username="FreshName"
        ),
        commit=True,
    )

    account = await transact(
        repository, lambda uow: uow.accounts.get_or_create_identity(IdentityProvider.DISCORD, "1"), commit=True
    )
    assert account.id is not None
    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(account_id=account.id, code="123456", consent=CONSENT),
        commit=True,
    )

    assert result.account is not None
    assert result.refresh is not None
    assert result.refresh.claimed_alias is None
    async with async_session_factory() as session:
        assert await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "FreshName")) is None
