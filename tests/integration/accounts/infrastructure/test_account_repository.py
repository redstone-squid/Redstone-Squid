"""Integration coverage for provider-neutral accounts and creator alias claiming."""

import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    AccountConsent,
    ClaimMethod,
    ClaimStatus,
    IdentityProvider,
)
from squid.accounts.domain import (
    AccountIdentity as AccountIdentityValue,
)
from squid.accounts.errors import AliasAlreadyClaimedError, CreatorAliasNotFoundError
from squid.accounts.infrastructure.models import (
    Account as AccountModel,
)
from squid.accounts.infrastructure.models import (
    AccountIdentity,
    CreatorAlias,
    CreatorAliasClaim,
    PublicCreatorRedirect,
    VerificationCode,
)
from squid.accounts.infrastructure.repository import AccountRepository
from squid.persistence.base import Base

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")
SECOND_MINECRAFT_UUID = UUID("22222222-2222-2222-2222-222222222222")

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["public_creator_redirects"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
]


@pytest.fixture
async def account_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    """Create the account tables from the models, then drop them again."""
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
) -> AccountRepository:
    del account_tables
    return AccountRepository(async_session_factory, "pepper")


async def _add_alias(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with session_factory() as session:
        alias = CreatorAlias(name=name)
        session.add(alias)
        await session.commit()
        return alias.id


async def _create_discord_account(repository: AccountRepository, discord_id: int):
    return await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),))


async def test_normalized_name_collapses_case_variants(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`Foo` and `foo` are the same creator alias."""
    await _add_alias(async_session_factory, "  Foo  ")

    async with async_session_factory() as session:
        session.add(CreatorAlias(name="foo"))
        with pytest.raises(IntegrityError):
            await session.commit()

    alias = await repository.get_alias_by_name("FOO")
    assert alias is not None
    assert alias.name == "  Foo  "


async def test_provider_subject_is_globally_unique(repository: AccountRepository) -> None:
    await _create_discord_account(repository, 1)

    with pytest.raises(IntegrityError):
        await _create_discord_account(repository, 1)


async def test_account_can_hold_discord_java_and_bedrock_identities(repository: AccountRepository) -> None:
    account = await repository.create(
        consent=CONSENT,
        identities=(
            AccountIdentityValue.discord(1),
            AccountIdentityValue.java(MINECRAFT_UUID, username="JavaPlayer"),
            AccountIdentityValue.bedrock(2**63, gamertag="BedrockPlayer"),
        ),
    )

    assert account.id is not None
    assert {identity.provider for identity in account.identities} == set(IdentityProvider)
    assert (await repository.get_by_identity(IdentityProvider.BEDROCK, str(2**63))) == account


async def test_concurrent_discord_resolution_creates_one_account(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await asyncio.gather(
        repository.get_or_create_discord(1),
        repository.get_or_create_discord(1),
    )

    assert first.id == second.id
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AccountModel)) == 1
        assert await session.scalar(select(func.count()).select_from(AccountIdentity)) == 1


async def test_claim_unclaimed_alias_matches_ignoring_case(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    alias_id = await _add_alias(async_session_factory, "player")
    assert account.id is not None

    claimed = await repository.claim_unclaimed_alias(
        account_id=account.id,
        name="PLAYER",
        method=ClaimMethod.VERIFIED_IGN,
    )

    assert claimed is not None
    assert claimed.id == alias_id
    assert claimed.account_id == account.id
    assert claimed.claim_method is ClaimMethod.VERIFIED_IGN
    assert claimed.public_creator_id == account.public_creator_id
    assert account.public_creator_id is not None
    profile = await repository.get_creator_profile(account.public_creator_id)
    assert profile is not None
    assert profile.aliases == ("player",)


async def test_concurrent_claims_leave_exactly_one_winner(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The `account_id IS NULL` predicate in the update prevents an overwrite."""
    first = await _create_discord_account(repository, 1)
    second = await _create_discord_account(repository, 2)
    await _add_alias(async_session_factory, "Player")
    assert first.id is not None
    assert second.id is not None

    results = await asyncio.gather(
        repository.claim_unclaimed_alias(account_id=first.id, name="Player", method=ClaimMethod.VERIFIED_IGN),
        repository.claim_unclaimed_alias(account_id=second.id, name="Player", method=ClaimMethod.VERIFIED_IGN),
    )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].account_id in {first.id, second.id}


async def test_verification_code_is_consumed_by_exactly_one_link(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await repository.replace_verification_code(
        minecraft_uuid=MINECRAFT_UUID,
        code="123456",
        username="Player",
    )

    first, second = await asyncio.gather(
        repository.consume_code_and_link_account(discord_id=1, code="123456", consent=CONSENT),
        repository.consume_code_and_link_account(discord_id=2, code="123456", consent=CONSENT),
    )

    assert sum(result.account is not None for result in (first, second)) == 1
    async with async_session_factory() as session:
        code = await session.scalar(select(VerificationCode))
        assert code is not None
        assert code.valid is False
        java_identities = (
            await session.scalars(
                select(AccountIdentity).where(
                    AccountIdentity.provider == IdentityProvider.JAVA,
                    AccountIdentity.subject == str(MINECRAFT_UUID),
                )
            )
        ).all()
        assert len(java_identities) == 1


async def test_unlink_removes_all_java_identities(repository: AccountRepository) -> None:
    account = await repository.create(
        consent=CONSENT,
        identities=(
            AccountIdentityValue.discord(1),
            AccountIdentityValue.java(MINECRAFT_UUID),
            AccountIdentityValue.java(SECOND_MINECRAFT_UUID),
        ),
    )

    assert await repository.unlink_java_identity(1)
    assert account.id is not None
    reloaded = await repository.get_by_id(account.id)
    assert reloaded is not None
    assert all(identity.provider is not IdentityProvider.JAVA for identity in reloaded.identities)


async def test_claim_request_and_approval_credits_the_claimant(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    staff = await _create_discord_account(repository, 7)
    alias_id = await _add_alias(async_session_factory, "OldName")
    assert account.id is not None
    assert staff.id is not None

    claim = await repository.request_claim(name="oldname", account_id=account.id)
    assert claim.alias_name == "OldName"
    assert [pending.id for pending in await repository.pending_claims()] == [claim.id]

    resolved = await repository.resolve_claim(
        claim_id=claim.id,
        status=ClaimStatus.APPROVED,
        resolved_by_account_id=staff.id,
    )

    assert resolved.status is ClaimStatus.APPROVED
    assert resolved.resolved_by_account_id == staff.id
    assert await repository.pending_claims() == ()
    async with async_session_factory() as session:
        alias = await session.get(CreatorAlias, alias_id)
        assert alias is not None
        assert alias.account_id == account.id
        assert alias.claim_method == ClaimMethod.STAFF_APPROVED


async def test_repeated_claim_requests_reuse_the_pending_row(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    await _add_alias(async_session_factory, "OldName")
    assert account.id is not None

    first = await repository.request_claim(name="OldName", account_id=account.id)
    second = await repository.request_claim(name="OldName", account_id=account.id)

    assert first.id == second.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(CreatorAliasClaim))).all()) == 1


async def test_claiming_a_held_credit_is_rejected(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    holder = await _create_discord_account(repository, 1)
    other = await _create_discord_account(repository, 2)
    await _add_alias(async_session_factory, "Player")
    assert holder.id is not None
    assert other.id is not None
    await repository.claim_unclaimed_alias(
        account_id=holder.id,
        name="Player",
        method=ClaimMethod.VERIFIED_IGN,
    )

    with pytest.raises(AliasAlreadyClaimedError):
        await repository.request_claim(name="Player", account_id=other.id)


async def test_claim_request_for_an_uncredited_name_is_rejected(repository: AccountRepository) -> None:
    account = await _create_discord_account(repository, 1)
    assert account.id is not None

    with pytest.raises(CreatorAliasNotFoundError):
        await repository.request_claim(name="NeverCredited", account_id=account.id)


async def test_public_creator_redirect_resolves_to_the_surviving_profile(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    assert account.id is not None
    assert account.public_creator_id is not None
    retired_id = UUID("33333333-3333-3333-3333-333333333333")
    await _add_alias(async_session_factory, "Builder")
    await repository.claim_unclaimed_alias(
        account_id=account.id,
        name="Builder",
        method=ClaimMethod.MIGRATED,
    )
    async with async_session_factory.begin() as session:
        session.add(PublicCreatorRedirect(retired_public_creator_id=retired_id, target_account_id=account.id))

    profile = await repository.get_creator_profile(retired_id)

    assert profile is not None
    assert profile.public_id == retired_id
    assert profile.canonical_public_id == account.public_creator_id
    assert profile.aliases == ("Builder",)
