"""Integration coverage for the composed public creator read."""

from functools import partial
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
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.accounts.models import CreatorAlias
from squid.builds import models as _build_models  # noqa: F401 — registers `builds` in metadata
from squid.builds.infrastructure import taxonomy as _taxonomy  # noqa: F401 — and its relationship targets
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Use the full migrated schema because account merges also transfer submission drafts and jobs."""
    return migrated_session_factory


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")


async def _credit_builds(
    session_factory: async_sessionmaker[AsyncSession], *, alias_name: str, submitter_id: int, count: int
) -> int:
    """Create *count* builds crediting a fresh alias, returning the alias id."""
    async with build_edit_session(session_factory) as session:
        alias = CreatorAlias(name=alias_name)
        session.add(alias)
        await session.flush()
        build_ids = (
            await session.scalars(
                insert(Base.metadata.tables["builds"])
                .values([{"submission_status": 1, "submitter_account_id": submitter_id, "category": "Other"}] * count)
                .returning(Base.metadata.tables["builds"].c.id)
            )
        ).all()
        await session.execute(
            insert(Base.metadata.tables["other_builds"]), [{"build_id": build_id} for build_id in build_ids]
        )
        await session.execute(
            insert(Base.metadata.tables["build_creators"]),
            [{"build_id": build_id, "alias_id": alias.id} for build_id in build_ids],
        )
        return alias.id


async def test_record_carries_profile_identities_and_credit_counts(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(JAVA_UUID, username="Notch"),
            ),
        ),
        commit=True,
    )
    assert account.id is not None
    assert account.public_creator_id is not None
    await _credit_builds(async_session_factory, alias_name="Notch", submitter_id=account.id, count=3)
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=account.id, name="Notch", method=ClaimMethod.VERIFIED_IGN
        ),
        commit=True,
    )
    await transact(
        repository,
        lambda uow: uow.accounts.upsert_profile(
            account.id, ProfileUpdate(display_name="Notch", bio="I build").validated()
        ),
        commit=True,
    )

    record = await transact(repository, lambda uow: uow.accounts.get_creator_profile_record(account.public_creator_id))

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
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    assert account.id is not None
    assert account.public_creator_id is not None
    await _credit_builds(async_session_factory, alias_name="Someone Else", submitter_id=account.id, count=2)
    async with build_edit_session(async_session_factory) as session:
        session.add(CreatorAlias(name="Uncredited"))
    await transact(
        repository,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=account.id, name="Uncredited", method=ClaimMethod.STAFF_APPROVED
        ),
        commit=True,
    )

    record = await transact(repository, lambda uow: uow.accounts.get_creator_profile_record(account.public_creator_id))

    assert record is not None
    assert [(alias.name, alias.build_count) for alias in record.aliases] == [("Uncredited", 0)]


async def test_record_follows_a_merge_redirect(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    surviving = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    absorbed = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    assert surviving.id is not None
    assert absorbed.id is not None
    retired_id = absorbed.public_creator_id
    assert retired_id is not None

    await transact(repository, lambda uow: uow.accounts.merge(surviving.id, absorbed.id), commit=True)
    record = await transact(repository, lambda uow: uow.accounts.get_creator_profile_record(retired_id))

    assert record is not None
    assert record.account_id == surviving.id
    assert record.public_id == retired_id
    assert record.canonical_public_id == surviving.public_creator_id
    assert present_public_profile(record).was_redirected


async def test_merge_leaves_the_surviving_profile_intact(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    surviving = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(1),)),
        commit=True,
    )
    absorbed = await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),)),
        commit=True,
    )
    assert surviving.id is not None
    assert absorbed.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.upsert_profile(surviving.id, ProfileUpdate(display_name="Keeper").validated()),
        commit=True,
    )
    await transact(
        repository,
        lambda uow: uow.accounts.upsert_profile(absorbed.id, ProfileUpdate(display_name="Absorbed").validated()),
        commit=True,
    )

    await transact(repository, lambda uow: uow.accounts.merge(surviving.id, absorbed.id), commit=True)

    profile = await transact(repository, lambda uow: uow.accounts.get_profile(surviving.id))
    assert profile is not None
    assert profile.display_name == "Keeper"
    # The absorbed account is gone, and its profile row went with it.
    assert await transact(repository, lambda uow: uow.accounts.get_profile(absorbed.id)) is None


async def test_unknown_public_id_returns_none(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    assert await transact(repository, lambda uow: uow.accounts.get_creator_profile_record(UUID(int=0))) is None
