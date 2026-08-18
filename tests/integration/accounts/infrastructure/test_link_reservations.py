"""Holding a verification code so a consent prompt can describe it.

The prompt runs before the code is redeemed, and the only code path used to be the atomic redemption
itself. A hold is what lets the prompt name the account it is about to link -- and, because a hold is
a write rather than a read, it is also what gives the attempt cap something to count.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, AccountIdentity, ClaimMethod
from squid.accounts.infrastructure.models import Account as AccountModel
from squid.accounts.infrastructure.models import AccountIdentity as AccountIdentityModel
from squid.accounts.infrastructure.models import CreatorAlias
from squid.accounts.infrastructure.repository import AccountRepository
from squid.builds.infrastructure import models as _build_models  # noqa: F401 — registers `builds` in metadata
from squid.builds.infrastructure import taxonomy as _taxonomy  # noqa: F401 — and its relationship targets
from squid.persistence.base import Base

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
CODE = "1234567890"
TTL = 180

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    Base.metadata.tables["public_creator_redirects"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
    Base.metadata.tables["verification_attempts"],
    # The preview counts build credits, so the association table and its `builds` parent are real
    # here rather than faked; `builds` only depends on `accounts`, which is already above.
    Base.metadata.tables["builds"],
    Base.metadata.tables["build_creators"],
]


@pytest.fixture
async def repository(
    async_engine: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AccountRepository]:
    async with async_engine.begin() as connection:
        # `builds` carries an embedding column; the container ships pgvector but only the migration
        # chain creates the extension, and this fixture uses `create_all`.
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield AccountRepository(async_session_factory, "pepper")
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _seed_code(repository: AccountRepository, *, username: str = "Notch") -> None:
    await repository.replace_verification_code(minecraft_uuid=JAVA_UUID, code=CODE, username=username)


async def test_reserving_previews_the_java_identity(repository: AccountRepository) -> None:
    await _seed_code(repository)

    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)

    assert reservation is not None
    assert reservation.preview.java_uuid == JAVA_UUID
    assert reservation.preview.username == "Notch"
    assert reservation.preview.credit is None  # nothing credits the name yet
    assert not reservation.preview.java_uuid_held_elsewhere
    assert reservation.expires_at > Instant.now()
    assert reservation.token


async def test_reserving_does_not_spend_the_code(repository: AccountRepository) -> None:
    """The hold must freeze the code, not consume it, or the preview would cost the redemption."""
    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None

    account = await repository.create(identities=[AccountIdentity.discord(1)])
    assert account.id is not None
    result = await repository.consume_code_and_link_account(
        account_id=account.id, code=CODE, consent=CONSENT, reservation_token=reservation.token
    )

    assert result.account is not None
    assert result.refresh is not None


async def test_a_held_code_cannot_be_reserved_twice(repository: AccountRepository) -> None:
    await _seed_code(repository)
    first = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert first is not None

    assert await repository.reserve_verification_code(CODE, ttl_seconds=TTL) is None


async def test_releasing_frees_the_code_immediately(repository: AccountRepository) -> None:
    await _seed_code(repository)
    first = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert first is not None

    assert await repository.release_verification_code(CODE, first.token) is True
    assert await repository.reserve_verification_code(CODE, ttl_seconds=TTL) is not None


async def test_releasing_is_idempotent_and_ignores_a_foreign_token(repository: AccountRepository) -> None:
    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None

    assert await repository.release_verification_code(CODE, "not-the-token") is False
    assert await repository.release_verification_code(CODE, reservation.token) is True
    assert await repository.release_verification_code(CODE, reservation.token) is False


async def test_a_lapsed_hold_frees_the_code_without_a_sweeper(repository: AccountRepository) -> None:
    await _seed_code(repository)
    lapsed = await repository.reserve_verification_code(CODE, ttl_seconds=-1)
    assert lapsed is not None

    assert await repository.reserve_verification_code(CODE, ttl_seconds=TTL) is not None


async def test_committing_with_a_lapsed_hold_is_reported_as_such(repository: AccountRepository) -> None:
    await _seed_code(repository)
    lapsed = await repository.reserve_verification_code(CODE, ttl_seconds=-1)
    assert lapsed is not None
    account = await repository.create(identities=[AccountIdentity.discord(1)])
    assert account.id is not None

    result = await repository.consume_code_and_link_account(
        account_id=account.id, code=CODE, consent=CONSENT, reservation_token=lapsed.token
    )

    assert result.reservation_expired is True
    assert result.account is None


async def test_committing_with_a_foreign_token_is_refused(repository: AccountRepository) -> None:
    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None
    account = await repository.create(identities=[AccountIdentity.discord(1)])
    assert account.id is not None

    result = await repository.consume_code_and_link_account(
        account_id=account.id, code=CODE, consent=CONSENT, reservation_token="wrong"
    )

    assert result.reservation_expired is True


async def test_the_token_is_stored_only_as_a_digest(repository: AccountRepository) -> None:
    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None

    async with repository._session_factory() as session:
        stored = await session.scalar(select(Base.metadata.tables["verification_codes"].c.reserved_token))

    assert stored is not None
    assert stored != reservation.token
    assert stored == repository.hash_verification_code(reservation.token)


async def test_a_cancelled_prompt_stores_nothing_about_the_user(repository: AccountRepository) -> None:
    """The notice promises exactly this, so it is asserted rather than assumed.

    Nothing here identifies the reserver, which is why the hold is keyed on a token instead of on an
    account: keying it on an account would have had to mint one in order to show a privacy prompt.
    """
    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None
    await repository.release_verification_code(CODE, reservation.token)

    async with repository._session_factory() as session:
        assert (await session.scalars(select(AccountModel))).all() == []
        assert (await session.scalars(select(AccountIdentityModel))).all() == []


async def test_the_preview_reports_an_unclaimed_credit_and_its_build_count(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_code(repository, username="Notch")
    submitter = await repository.create(identities=[AccountIdentity.discord(7)])
    async with async_session_factory.begin() as session:
        alias = CreatorAlias(name="Notch")
        session.add(alias)
        await session.flush()
        # Core inserts rather than the ORM dataclass: `Build` requires eight keyword arguments that
        # say nothing about a credit count, and only these two columns are NOT NULL in the table.
        build_ids = (
            await session.scalars(
                insert(Base.metadata.tables["builds"])
                .values([{"submission_status": 1, "submitter_account_id": submitter.id}] * 3)
                .returning(Base.metadata.tables["builds"].c.id)
            )
        ).all()
        await session.execute(
            insert(Base.metadata.tables["build_creators"]),
            [{"build_id": build_id, "alias_id": alias.id} for build_id in build_ids],
        )

    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)

    assert reservation is not None
    credit = reservation.preview.credit
    assert credit is not None
    assert credit.name == "Notch"
    assert credit.build_count == 3
    assert not credit.is_contested


async def test_the_preview_reports_a_contested_credit_by_public_creator(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Agreeing would move nothing here, so the prompt has to say so before the button is pressed."""
    await _seed_code(repository, username="Notch")
    holder = await repository.create(identities=[AccountIdentity.discord(999)])
    assert holder.id is not None
    async with async_session_factory.begin() as session:
        session.add(
            CreatorAlias(
                name="Notch",
                account_id=holder.id,
                claimed_at=Instant.now(),
                claim_method=ClaimMethod.STAFF_APPROVED,
            )
        )

    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)

    assert reservation is not None
    credit = reservation.preview.credit
    assert credit is not None
    assert credit.is_contested
    assert credit.held_by_public_creator_id == holder.public_creator_id


async def test_the_preview_reports_a_java_uuid_already_linked_elsewhere(repository: AccountRepository) -> None:
    await _seed_code(repository)
    await repository.create(identities=[AccountIdentity.java(JAVA_UUID, username="Notch")])

    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)

    assert reservation is not None
    assert reservation.preview.java_uuid_held_elsewhere is True


async def test_the_preview_folds_the_username_to_find_the_credit(repository: AccountRepository) -> None:
    """The credit is matched by the same folding that defines creator identity, not by spelling."""
    await _seed_code(repository, username="NOTCH")
    async with repository._session_factory.begin() as session:
        session.add(CreatorAlias(name="notch"))

    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)

    assert reservation is not None
    assert reservation.preview.credit is not None
    assert reservation.preview.credit.name == "notch"


async def test_an_unknown_or_spent_code_cannot_be_reserved(repository: AccountRepository) -> None:
    assert await repository.reserve_verification_code(CODE, ttl_seconds=TTL) is None

    await _seed_code(repository)
    reservation = await repository.reserve_verification_code(CODE, ttl_seconds=TTL)
    assert reservation is not None
    account = await repository.create(identities=[AccountIdentity.discord(1)])
    assert account.id is not None
    await repository.consume_code_and_link_account(
        account_id=account.id, code=CODE, consent=CONSENT, reservation_token=reservation.token
    )

    # Spent codes are invalidated, so the hold cannot be retaken to replay the link.
    assert await repository.reserve_verification_code(CODE, ttl_seconds=TTL) is None
