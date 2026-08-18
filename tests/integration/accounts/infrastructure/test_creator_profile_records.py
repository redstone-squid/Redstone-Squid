"""Integration coverage for the composed public creator read."""

from uuid import UUID

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    AccountConsent,
    ClaimMethod,
    IdentityProvider,
    ProfileUpdate,
    present_public_profile,
)
from squid.accounts.domain import (
    AccountIdentity as AccountIdentityValue,
)
from squid.accounts.infrastructure.models import CreatorAlias
from squid.accounts.infrastructure.repository import AccountRepository
from squid.builds.infrastructure import models as _build_models  # noqa: F401 — registers `builds` in metadata
from squid.builds.infrastructure import taxonomy as _taxonomy  # noqa: F401 — and its relationship targets
from squid.persistence.base import Base

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Use the migrated schema, not `create_all`.

    A merge reaches into submission drafts and finalization jobs, so this needs the whole schema;
    running the real chain also proves the profile migration produces what the models describe.
    """
    return migrated_session_factory


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> AccountRepository:
    return AccountRepository(async_session_factory, "pepper")


async def _credit_builds(
    session_factory: async_sessionmaker[AsyncSession], *, alias_name: str, submitter_id: int, count: int
) -> int:
    """Create *count* builds crediting a fresh alias, returning the alias id."""
    async with session_factory.begin() as session:
        alias = CreatorAlias(name=alias_name)
        session.add(alias)
        await session.flush()
        build_ids = (
            await session.scalars(
                insert(Base.metadata.tables["builds"])
                .values([{"submission_status": 1, "submitter_account_id": submitter_id}] * count)
                .returning(Base.metadata.tables["builds"].c.id)
            )
        ).all()
        await session.execute(
            insert(Base.metadata.tables["build_creators"]),
            [{"build_id": build_id, "alias_id": alias.id} for build_id in build_ids],
        )
        return alias.id


async def test_record_carries_profile_identities_and_credit_counts(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await repository.create(
        consent=CONSENT,
        identities=(
            AccountIdentityValue.discord(1),
            AccountIdentityValue.java(JAVA_UUID, username="Notch"),
        ),
    )
    assert account.id is not None
    assert account.public_creator_id is not None
    await _credit_builds(async_session_factory, alias_name="Notch", submitter_id=account.id, count=3)
    await repository.claim_unclaimed_alias(account_id=account.id, name="Notch", method=ClaimMethod.VERIFIED_IGN)
    await repository.upsert_profile(account.id, ProfileUpdate(display_name="Notch", bio="I build").validated())

    record = await repository.get_creator_profile_record(account.public_creator_id)

    assert record is not None
    assert record.profile.display_name == "Notch"
    assert record.profile.bio == "I build"
    assert [alias.name for alias in record.aliases] == ["Notch"]
    assert record.aliases[0].build_count == 3
    assert {identity.provider for identity in record.identities} == {
        IdentityProvider.DISCORD,
        IdentityProvider.JAVA,
    }
    assert record.joined_at is not None


async def test_unclaimed_names_do_not_appear_and_uncredited_names_count_zero(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),))
    assert account.id is not None
    assert account.public_creator_id is not None
    await _credit_builds(async_session_factory, alias_name="Someone Else", submitter_id=account.id, count=2)
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Uncredited"))
    await repository.claim_unclaimed_alias(account_id=account.id, name="Uncredited", method=ClaimMethod.STAFF_APPROVED)

    record = await repository.get_creator_profile_record(account.public_creator_id)

    assert record is not None
    assert [(alias.name, alias.build_count) for alias in record.aliases] == [("Uncredited", 0)]


async def test_record_follows_a_merge_redirect(repository: AccountRepository) -> None:
    surviving = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),))
    absorbed = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
    assert surviving.id is not None
    assert absorbed.id is not None
    retired_id = absorbed.public_creator_id
    assert retired_id is not None

    await repository.merge(surviving.id, absorbed.id)
    record = await repository.get_creator_profile_record(retired_id)

    assert record is not None
    assert record.account_id == surviving.id
    assert record.public_id == retired_id
    assert record.canonical_public_id == surviving.public_creator_id
    assert present_public_profile(record).was_redirected


async def test_merge_leaves_the_surviving_profile_intact(repository: AccountRepository) -> None:
    surviving = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),))
    absorbed = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
    assert surviving.id is not None
    assert absorbed.id is not None
    await repository.upsert_profile(surviving.id, ProfileUpdate(display_name="Keeper").validated())
    await repository.upsert_profile(absorbed.id, ProfileUpdate(display_name="Absorbed").validated())

    await repository.merge(surviving.id, absorbed.id)

    profile = await repository.get_profile(surviving.id)
    assert profile is not None
    assert profile.display_name == "Keeper"
    # The absorbed account is gone, and its profile row went with it.
    assert await repository.get_profile(absorbed.id) is None


async def test_unknown_public_id_returns_none(repository: AccountRepository) -> None:
    assert await repository.get_creator_profile_record(UUID(int=0)) is None
