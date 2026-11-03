"""Holding a verification code so a consent prompt can describe it.

The prompt runs before the code is redeemed, and the only code path used to be the atomic redemption
itself. A hold is what lets the prompt name the account it is about to link -- and, because a hold is
a write rather than a read, it is also what gives the attempt cap something to count.
"""

from collections.abc import AsyncIterator
from functools import partial
from uuid import UUID

import anyio
import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, ClaimMethod, LinkReservation
from squid.accounts.models import Account as AccountModel
from squid.accounts.models import AccountIdentity, CreatorAlias
from squid.accounts.models import AccountIdentity as AccountIdentityModel
from squid.builds import models as _build_models  # noqa: F401 — registers `builds` in metadata
from squid.builds.infrastructure import taxonomy as _taxonomy  # noqa: F401 — and its relationship targets
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

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
) -> AsyncIterator[UnitOfWorkFactory[SqlAlchemyUnitOfWork]]:
    async with async_engine.begin() as connection:
        # `builds` carries an embedding column; the container ships pgvector but only the migration
        # chain creates the extension, and this fixture uses `create_all`.
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _seed_code(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], *, username: str = "Notch") -> None:
    await transact(
        repository,
        lambda uow: uow.accounts.replace_verification_code(minecraft_uuid=JAVA_UUID, code=CODE, username=username),
        commit=True,
    )


async def test_reserving_previews_the_java_identity(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    await _seed_code(repository)

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )

    assert reservation is not None
    assert reservation.preview.java_uuid == JAVA_UUID
    assert reservation.preview.username == "Notch"
    assert reservation.preview.credit is None  # nothing credits the name yet
    assert not reservation.preview.java_uuid_held_elsewhere
    assert reservation.expires_at > Instant.now()
    assert reservation.token


async def test_reserving_does_not_spend_the_code(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """The hold must freeze the code, not consume it, or the preview would cost the redemption."""
    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None

    account = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(1)]), commit=True
    )
    assert account.id is not None
    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(
            account_id=account.id, code=CODE, consent=CONSENT, reservation_token=reservation.token
        ),
        commit=True,
    )

    assert result.account is not None
    assert result.refresh is not None


async def test_a_held_code_cannot_be_reserved_twice(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    await _seed_code(repository)
    first = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert first is not None

    assert (
        await transact(
            repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
        )
        is None
    )


async def test_concurrent_reservations_issue_exactly_one_hold(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    await _seed_code(repository)
    start = anyio.Event()
    reservations: list[LinkReservation | None] = []

    async def reserve() -> None:
        await start.wait()
        reservations.append(
            await transact(
                repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reserve)
        tasks.start_soon(reserve)
        start.set()

    assert len(reservations) == 2
    assert sum(reservation is not None for reservation in reservations) == 1


async def test_releasing_frees_the_code_immediately(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    await _seed_code(repository)
    first = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert first is not None

    assert (
        await transact(repository, lambda uow: uow.accounts.release_verification_code(CODE, first.token), commit=True)
        is True
    )
    assert (
        await transact(
            repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
        )
        is not None
    )


async def test_releasing_is_idempotent_and_ignores_a_foreign_token(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None

    assert (
        await transact(
            repository, lambda uow: uow.accounts.release_verification_code(CODE, "not-the-token"), commit=True
        )
        is False
    )
    assert (
        await transact(
            repository, lambda uow: uow.accounts.release_verification_code(CODE, reservation.token), commit=True
        )
        is True
    )
    assert (
        await transact(
            repository, lambda uow: uow.accounts.release_verification_code(CODE, reservation.token), commit=True
        )
        is False
    )


async def test_a_lapsed_hold_frees_the_code_without_a_sweeper(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    await _seed_code(repository)
    lapsed = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=-1), commit=True
    )
    assert lapsed is not None

    assert (
        await transact(
            repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
        )
        is not None
    )


async def test_committing_with_a_lapsed_hold_is_reported_as_such(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    await _seed_code(repository)
    lapsed = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=-1), commit=True
    )
    assert lapsed is not None
    account = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(1)]), commit=True
    )
    assert account.id is not None

    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(
            account_id=account.id, code=CODE, consent=CONSENT, reservation_token=lapsed.token
        ),
        commit=True,
    )

    assert result.reservation_expired is True
    assert result.account is None


async def test_committing_with_a_foreign_token_is_refused(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None
    account = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(1)]), commit=True
    )
    assert account.id is not None

    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(
            account_id=account.id, code=CODE, consent=CONSENT, reservation_token="wrong"
        ),
        commit=True,
    )

    assert result.reservation_expired is True


async def test_the_token_is_stored_only_as_a_digest(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None

    async with async_session_factory() as session:
        stored = await session.scalar(select(Base.metadata.tables["verification_codes"].c.reserved_token))

    assert stored is not None
    assert stored != reservation.token
    async with repository() as uow:
        assert stored == uow.accounts.hash_verification_code(reservation.token)


async def test_a_cancelled_prompt_stores_nothing_about_the_user(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The notice promises exactly this, so it is asserted rather than assumed.

    Nothing here identifies the reserver, which is why the hold is keyed on a token instead of on an
    account: keying it on an account would have had to mint one in order to show a privacy prompt.
    """
    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None
    await transact(repository, lambda uow: uow.accounts.release_verification_code(CODE, reservation.token), commit=True)

    async with async_session_factory() as session:
        assert (await session.scalars(select(AccountModel))).all() == []
        assert (await session.scalars(select(AccountIdentityModel))).all() == []


async def test_the_preview_reports_an_unclaimed_credit_and_its_build_count(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_code(repository, username="Notch")
    submitter = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(7)]), commit=True
    )
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

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )

    assert reservation is not None
    credit = reservation.preview.credit
    assert credit is not None
    assert credit.name == "Notch"
    assert credit.build_count == 3
    assert not credit.is_contested


async def test_the_preview_reports_a_contested_credit_by_public_creator(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Agreeing would move nothing here, so the prompt has to say so before the button is pressed."""
    await _seed_code(repository, username="Notch")
    holder = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(999)]), commit=True
    )
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

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )

    assert reservation is not None
    credit = reservation.preview.credit
    assert credit is not None
    assert credit.is_contested
    assert credit.held_by_public_creator_id == holder.public_creator_id


async def test_an_unclaimed_preview_can_become_contested_before_commit(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The atomic commit must re-evaluate ownership changed while the reader considered the preview."""
    await _seed_code(repository, username="Notch")
    claimant = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(1)]), commit=True
    )
    holder = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(2)]), commit=True
    )
    assert claimant.id is not None
    assert holder.id is not None
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="Notch"))

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None
    assert reservation.preview.credit is not None
    assert not reservation.preview.credit.is_contested

    async with async_session_factory.begin() as session:
        await session.execute(
            update(CreatorAlias)
            .where(CreatorAlias.name == "Notch")
            .values(account_id=holder.id, claimed_at=Instant.now(), claim_method=ClaimMethod.STAFF_APPROVED)
        )

    result = await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(
            account_id=claimant.id, code=CODE, consent=CONSENT, reservation_token=reservation.token
        ),
        commit=True,
    )

    assert result.refresh is not None
    assert result.refresh.contested_alias is not None
    assert result.refresh.contested_alias.account_id == holder.id
    assert result.refresh.opened_claim is not None
    assert result.refresh.opened_claim.account_id == claimant.id


async def test_the_preview_reports_a_java_uuid_already_linked_elsewhere(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    await _seed_code(repository)
    await transact(
        repository,
        lambda uow: uow.accounts.create(identities=[AccountIdentity.java(JAVA_UUID, username="Notch")]),
        commit=True,
    )

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )

    assert reservation is not None
    assert reservation.preview.java_uuid_held_elsewhere is True


async def test_the_preview_folds_the_username_to_find_the_credit(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The credit is matched by the same folding that defines creator identity, not by spelling."""
    await _seed_code(repository, username="NOTCH")
    async with async_session_factory.begin() as session:
        session.add(CreatorAlias(name="notch"))

    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )

    assert reservation is not None
    assert reservation.preview.credit is not None
    assert reservation.preview.credit.name == "notch"


async def test_an_unknown_or_spent_code_cannot_be_reserved(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    assert (
        await transact(
            repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
        )
        is None
    )

    await _seed_code(repository)
    reservation = await transact(
        repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
    )
    assert reservation is not None
    account = await transact(
        repository, lambda uow: uow.accounts.create(identities=[AccountIdentity.discord(1)]), commit=True
    )
    assert account.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.consume_code_and_link_account(
            account_id=account.id, code=CODE, consent=CONSENT, reservation_token=reservation.token
        ),
        commit=True,
    )

    # Spent codes are invalidated, so the hold cannot be retaken to replay the link.
    assert (
        await transact(
            repository, lambda uow: uow.accounts.reserve_verification_code(CODE, ttl_seconds=TTL), commit=True
        )
        is None
    )
