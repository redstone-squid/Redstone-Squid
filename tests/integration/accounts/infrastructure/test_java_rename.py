"""Integration coverage for the Java rename reconcile.

A Minecraft rename used to be a side effect of relinking: the display name was overwritten and an
already-existing unclaimed alias was opportunistically claimed. Everything else was silent — a new
name no build had credited yet produced no credit at all, and a name held by someone else was
indistinguishable from "nothing to claim".

`refresh_java_identity` makes the four cases exhaustive, and shares `_reconcile_java_name` with the
link path so both behave identically.
"""

import asyncio
from collections.abc import AsyncGenerator
from functools import partial
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    AccountConsent,
    ClaimMethod,
    ClaimStatus,
    IdentityProvider,
)
from squid.accounts.errors import AliasAlreadyClaimedError, MinecraftAccountNotFoundError
from squid.accounts.models import AccountIdentity, AliasClaim, CreatorAlias
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    Base.metadata.tables["public_creator_redirects"],
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


async def test_rename_creates_and_claims_an_uncredited_name(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The old behaviour left a renamed user with no credit until someone credited the new name."""
    account_id = await _linked_account_id(repository, username="OldName")

    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )

    assert refresh.renamed is True
    assert refresh.previous_name == "OldName"
    assert refresh.claimed_alias is not None
    assert refresh.claimed_alias.name == "NewName"
    assert refresh.claimed_alias.claim_method is ClaimMethod.VERIFIED_IGN
    assert refresh.claimed_alias.account_id == account_id
    async with async_session_factory() as session:
        stored = await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "NewName"))
        assert stored is not None
        assert stored.account_id == account_id


async def test_rename_claims_an_existing_unclaimed_name(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _linked_account_id(repository, username="OldName")
    alias_id = await _add_alias(async_session_factory, "NewName")

    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )

    assert refresh.claimed_alias is not None
    assert refresh.claimed_alias.id == alias_id
    assert refresh.is_contested is False


async def test_rename_retains_credit_under_the_previous_name(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A rename does not retract credit for work published under the old name."""
    account_id = await _linked_account_id(repository, username="OldName")
    await _add_alias(async_session_factory, "OldName")
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=account_id, name="OldName", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )

    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(account_id=account_id, java_uuid=JAVA_UUID, username="NewName"),
        commit=True,
    )

    assert refresh.retained_alias_names == ("OldName",)
    assert refresh.claimed_alias is not None
    assert refresh.claimed_alias.name == "NewName"


async def test_rename_into_a_held_name_opens_a_claim_and_transfers_nothing(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The case that used to be silent: someone else already owns the new name."""
    account_id = await _linked_account_id(repository, discord_id=1, username="OldName")
    holder = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    assert holder.id is not None
    await _add_alias(async_session_factory, "Contested")
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=holder.id, name="Contested", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )

    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(
            account_id=account_id, java_uuid=JAVA_UUID, username="Contested"
        ),
        commit=True,
    )

    assert refresh.is_contested is True
    assert refresh.claimed_alias is None
    assert refresh.contested_alias is not None
    assert refresh.contested_alias.account_id == holder.id, "the credit must not move"
    assert refresh.opened_claim is not None
    assert refresh.opened_claim.status is ClaimStatus.PENDING
    assert [claim.id for claim in await transact(repository, lambda uow: uow.accounts.pending_claims())] == [
        refresh.opened_claim.id
    ]


async def test_repeated_refresh_into_a_held_name_reuses_one_claim(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _linked_account_id(repository, discord_id=1, username="OldName")
    holder = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    assert holder.id is not None
    await _add_alias(async_session_factory, "Contested")
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=holder.id, name="Contested", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )

    first = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(
            account_id=account_id, java_uuid=JAVA_UUID, username="Contested"
        ),
        commit=True,
    )
    second = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(
            account_id=account_id, java_uuid=JAVA_UUID, username="Contested"
        ),
        commit=True,
    )

    assert first.opened_claim is not None
    assert second.opened_claim is not None
    assert first.opened_claim.id == second.opened_claim.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(AliasClaim))).all()) == 1


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

    assert first.claimed_alias is not None
    assert second.claimed_alias is not None
    assert first.claimed_alias.id == second.claimed_alias.id
    assert second.renamed is False, "the second refresh sees the name it just stored"


async def test_concurrent_refresh_produces_one_claim(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _linked_account_id(repository, username="OldName")

    results = await asyncio.gather(
        transact(
            repository,
            lambda uow: uow.accounts.refresh_java_identity(
                account_id=account_id, java_uuid=JAVA_UUID, username="NewName"
            ),
            commit=True,
        ),
        transact(
            repository,
            lambda uow: uow.accounts.refresh_java_identity(
                account_id=account_id, java_uuid=JAVA_UUID, username="NewName"
            ),
            commit=True,
        ),
    )

    claimed = {result.claimed_alias.id for result in results if result.claimed_alias is not None}
    assert len(claimed) == 1
    async with async_session_factory() as session:
        aliases = (await session.scalars(select(CreatorAlias).where(CreatorAlias.name == "NewName"))).all()
        assert len(aliases) == 1


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


async def test_approving_a_contested_claim_needs_reassign(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Staff can move a credit, but only by saying so."""
    account_id = await _linked_account_id(repository, discord_id=1, username="OldName")
    holder = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    staff = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(3),)),
        commit=True,
    )
    assert holder.id is not None
    assert staff.id is not None
    await _add_alias(async_session_factory, "Contested")
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=holder.id, name="Contested", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )
    refresh = await transact(
        repository,
        lambda uow: uow.accounts.refresh_java_identity(
            account_id=account_id, java_uuid=JAVA_UUID, username="Contested"
        ),
        commit=True,
    )
    assert refresh.opened_claim is not None

    with pytest.raises(AliasAlreadyClaimedError):
        await transact(
            repository,
            lambda uow: uow.accounts.resolve_claim(
                claim_id=refresh.opened_claim.id, status=ClaimStatus.APPROVED, resolved_by_account_id=staff.id
            ),
            commit=True,
        )

    resolved = await transact(
        repository,
        lambda uow: uow.accounts.resolve_claim(
            claim_id=refresh.opened_claim.id,
            status=ClaimStatus.APPROVED,
            resolved_by_account_id=staff.id,
            reassign=True,
        ),
        commit=True,
    )

    assert resolved.status is ClaimStatus.APPROVED
    async with async_session_factory() as session:
        alias = await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "Contested"))
        assert alias is not None
        assert alias.account_id == account_id
        assert alias.claim_method == ClaimMethod.STAFF_APPROVED


async def test_linking_reconciles_the_same_way_a_refresh_does(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The point of sharing `_reconcile_java_name`: linking a renamed account claims the new name."""
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
    assert result.refresh.claimed_alias is not None
    assert result.refresh.claimed_alias.name == "FreshName"
    assert result.claimed_alias == result.refresh.claimed_alias
    async with async_session_factory() as session:
        stored = await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "FreshName"))
        assert stored is not None
        assert stored.account_id == result.account.id
