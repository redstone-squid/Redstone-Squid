"""Integration coverage for account-keyed identities and creator alias claiming."""

import asyncio
from functools import partial
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import (
    PRIVACY_NOTICE_VERSION,
    ClaimStatus,
    IdentityProvider,
    PrivacyNoticeReceipt,
)
from squid.core.accounts.errors import AccountNotFoundError, AliasAlreadyClaimedError, CreatorAliasNotFoundError
from squid.core.accounts.models import (
    Account as AccountModel,
)
from squid.core.accounts.models import (
    AccountIdentity,
    AliasClaim,
    CreatorAlias,
    PublicAccountRedirect,
    VerificationCode,
)
from squid.core.accounts.models import AccountIdentity as AccountIdentityValue
from squid.core.builds.models import BuildCredit
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.unit_of_work import UnitOfWorkFactory
from tests.support.build_edits import transact_build_edit as transact
from tests.support.creator_credits import publish_credits

CONSENT = PrivacyNoticeReceipt(PRIVACY_NOTICE_VERSION, Instant.from_utc(2026, 8, 4))
MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")
SECOND_MINECRAFT_UUID = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    return migrated_session_factory


@pytest.fixture
def repository(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")


async def _add_alias(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    (credit,) = await publish_credits(session_factory, [name])
    assert credit.alias_id is not None
    return credit.alias_id


async def _create_discord_account(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], discord_id: int):
    return await transact(
        repository,
        lambda uow, discord_id=discord_id: uow.accounts.create(
            consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),)
        ),
        commit=True,
    )


async def test_normalized_name_collapses_case_variants(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`Foo` and `foo` are the same creator alias."""
    await _add_alias(async_session_factory, "  Foo  ")

    async with async_session_factory() as session:
        session.add(CreatorAlias(name="foo"))
        with pytest.raises(IntegrityError):
            await session.commit()

    alias = await transact(repository, lambda uow: uow.accounts.get_alias_by_name("FOO"))
    assert alias is not None
    assert alias.name == "  Foo  "


async def test_an_alias_is_found_by_id(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alias_id = await _add_alias(async_session_factory, "Foo")

    alias = await transact(repository, lambda uow: uow.accounts.get_alias(alias_id))
    missing = await transact(repository, lambda uow: uow.accounts.get_alias(alias_id + 1))

    assert alias is not None
    assert alias.name == "Foo"
    assert missing is None


async def test_provider_subject_is_globally_unique(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    await _create_discord_account(repository, 1)

    with pytest.raises(IntegrityError):
        await _create_discord_account(repository, 1)


async def test_account_can_hold_discord_java_and_bedrock_identities(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(MINECRAFT_UUID, username="JavaPlayer"),
                AccountIdentityValue.bedrock(2**63, gamertag="BedrockPlayer"),
            ),
        ),
        commit=True,
    )

    assert account.id is not None
    assert {identity.provider for identity in account.identities} == set(IdentityProvider)
    loaded = await transact(repository, lambda uow: uow.accounts.get_by_identity(IdentityProvider.BEDROCK, str(2**63)))
    assert loaded is not None
    assert loaded.id == account.id


@pytest.mark.parametrize("provider", [IdentityProvider.DISCORD, IdentityProvider.BEDROCK])
async def test_concurrent_identity_resolution_creates_one_account(
    provider: IdentityProvider,
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The race-safe insert is per-namespace, not Discord's alone."""
    first, second = await asyncio.gather(
        transact(repository, lambda uow: uow.accounts.get_or_create_identity(provider, "1"), commit=True),
        transact(repository, lambda uow: uow.accounts.get_or_create_identity(provider, "1"), commit=True),
    )

    assert first.id == second.id
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AccountModel)) == 1
        assert await session.scalar(select(func.count()).select_from(AccountIdentity)) == 1


async def test_verification_code_is_consumed_by_exactly_one_link(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await transact(
        repository,
        lambda uow: uow.accounts.replace_verification_code(
            minecraft_uuid=MINECRAFT_UUID, code="123456", username="Player"
        ),
        commit=True,
    )

    one = await _create_discord_account(repository, 1)
    two = await _create_discord_account(repository, 2)
    assert one.id is not None
    assert two.id is not None

    first, second = await asyncio.gather(
        transact(
            repository,
            lambda uow: uow.accounts.consume_code_and_link_account(account_id=one.id, code="123456", consent=CONSENT),
            commit=True,
        ),
        transact(
            repository,
            lambda uow: uow.accounts.consume_code_and_link_account(account_id=two.id, code="123456", consent=CONSENT),
            commit=True,
        ),
    )

    # Both callers now have an account, so both results carry one; exactly one link lands.
    assert sum(result.claimed_alias is not None or result.refresh is not None for result in (first, second)) == 1
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


async def test_linking_a_code_mints_no_identity_outside_the_java_namespace(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Java verification code links only a Java identity, even when the caller has no Discord identity."""
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.bedrock(99),)),
        commit=True,
    )
    assert account.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.replace_verification_code(
            minecraft_uuid=MINECRAFT_UUID, code="123456", username="Player"
        ),
        commit=True,
    )

    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(account_id=account.id, code="123456", consent=CONSENT),
        commit=True,
    )

    assert result.account is not None
    assert {identity.provider for identity in result.account.identities} == {
        IdentityProvider.BEDROCK,
        IdentityProvider.JAVA,
    }
    async with async_session_factory() as session:
        discord_identities = await session.scalar(
            select(func.count())
            .select_from(AccountIdentity)
            .where(AccountIdentity.provider == IdentityProvider.DISCORD)
        )
        assert discord_identities == 0


async def test_linking_a_code_for_a_missing_account_creates_nothing(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await transact(
        repository,
        lambda uow: uow.accounts.replace_verification_code(
            minecraft_uuid=MINECRAFT_UUID, code="123456", username="Player"
        ),
        commit=True,
    )

    with pytest.raises(AccountNotFoundError):
        await transact(
            repository,
            lambda uow: uow.accounts.consume_code_and_link_account(account_id=999, code="123456", consent=CONSENT),
            commit=True,
        )

    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AccountModel)) == 0


async def test_unlink_removes_all_java_identities(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(MINECRAFT_UUID),
                AccountIdentityValue.java(SECOND_MINECRAFT_UUID),
            ),
        ),
        commit=True,
    )

    assert account.id is not None
    java = [identity for identity in account.identities if identity.provider is IdentityProvider.JAVA]
    assert len(java) == 2
    # Unlink by identity ID so each Java login can be removed independently.
    for identity in java:
        assert identity.id is not None
        assert (
            await transact(
                repository,
                lambda uow, identity=identity: uow.accounts.unlink_identity(account.id, identity.id),
                commit=True,
            )
            is not None
        )
    reloaded = await transact(repository, lambda uow: uow.accounts.get_by_id(account.id))
    assert reloaded is not None
    assert all(identity.provider is not IdentityProvider.JAVA for identity in reloaded.identities)


async def test_claim_request_and_approval_credits_the_claimant(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    staff = await _create_discord_account(repository, 7)
    alias_id = await _add_alias(async_session_factory, "OldName")
    assert account.id is not None
    assert staff.id is not None

    claim = await transact(
        repository, lambda uow: uow.accounts.request_claim(name="oldname", account_id=account.id), commit=True
    )
    assert claim.alias_name == "OldName"
    assert [pending.id for pending in await transact(repository, lambda uow: uow.accounts.pending_claims())] == [
        claim.id
    ]

    resolved = await transact(
        repository,
        lambda uow: uow.accounts.resolve_claim(
            claim_id=claim.id, status=ClaimStatus.APPROVED, resolved_by_account_id=staff.id
        ),
        commit=True,
    )

    assert resolved.status is ClaimStatus.APPROVED
    assert resolved.resolved_by_account_id == staff.id
    assert await transact(repository, lambda uow: uow.accounts.pending_claims()) == ()
    async with async_session_factory() as session:
        alias = await session.get(CreatorAlias, alias_id)
        assert alias is not None
        assert alias.account_id is None
        credit = await session.scalar(select(BuildCredit).where(BuildCredit.alias_id == alias_id))
        assert credit is not None
        assert credit.account_id == account.id
        assert credit.account_source == "reviewed"


async def test_repeated_claim_requests_reuse_the_pending_row(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    await _add_alias(async_session_factory, "OldName")
    assert account.id is not None

    first = await transact(
        repository, lambda uow: uow.accounts.request_claim(name="OldName", account_id=account.id), commit=True
    )
    second = await transact(
        repository, lambda uow: uow.accounts.request_claim(name="OldName", account_id=account.id), commit=True
    )

    assert first.id == second.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(AliasClaim))).all()) == 1


async def test_claiming_a_held_credit_is_rejected(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    holder = await _create_discord_account(repository, 1)
    other = await _create_discord_account(repository, 2)
    await publish_credits(async_session_factory, ["Player"], owner=holder.id)
    claim = await transact(
        repository, lambda uow: uow.accounts.request_claim(name="Player", account_id=other.id), commit=True
    )
    with pytest.raises(AliasAlreadyClaimedError):
        await transact(
            repository,
            lambda uow: uow.accounts.resolve_claim(
                claim_id=claim.id, status=ClaimStatus.APPROVED, resolved_by_account_id=other.id
            ),
            commit=True,
        )


async def test_claim_request_for_an_uncredited_name_is_rejected(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    account = await _create_discord_account(repository, 1)
    assert account.id is not None

    with pytest.raises(CreatorAliasNotFoundError):
        await transact(
            repository, lambda uow: uow.accounts.request_claim(name="NeverCredited", account_id=account.id), commit=True
        )


async def test_public_creator_redirect_resolves_to_the_surviving_profile(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _create_discord_account(repository, 1)
    assert account.id is not None
    assert account.public_account_id is not None
    retired_id = UUID("33333333-3333-3333-3333-333333333333")
    await publish_credits(async_session_factory, ["Builder"], owner=account.id)
    async with async_session_factory.begin() as session:
        session.add(PublicAccountRedirect(retired_public_account_id=retired_id, target_account_id=account.id))

    profile = await transact(repository, lambda uow: uow.accounts.get_creator_profile(retired_id))

    assert profile is not None
    assert profile.public_id == retired_id
    assert profile.canonical_public_id == account.public_account_id
    assert profile.aliases == ("Builder",)
