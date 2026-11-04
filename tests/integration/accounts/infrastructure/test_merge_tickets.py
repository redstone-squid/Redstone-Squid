"""Integration coverage for the one-time codes that prove both sides of a merge."""

from functools import partial

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    MERGE_TICKET_TTL_SECONDS,
    AccountConsent,
    AccountMerge,
)
from squid.accounts.errors import AccountNotFoundError
from squid.accounts.infrastructure.repository import AccountRepository
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.accounts.models import MergeTicket as AccountMergeTicketModel
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Merging reaches across the schema, so this runs against the real migration chain."""
    return migrated_session_factory


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, async_session_factory, verification_code_pepper="pepper")


async def _account(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], discord_id: int):
    return await transact(
        repository,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),)),
        commit=True,
    )


async def test_a_ticket_round_trips_and_is_spent_once(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    account = await _account(repository, 1)
    assert account.id is not None

    ticket = await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(account.id, "CODE1234", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )
    assert ticket.account_id == account.id
    assert ticket.expires_at > ticket.created_at

    assert (await transact(repository, lambda uow: uow.accounts.peek_merge_ticket("CODE1234"))) is not None
    assert (
        await transact(repository, lambda uow: uow.accounts.consume_merge_ticket("CODE1234"), commit=True)
    ) is not None
    # Spent: the delete is the redemption, so a replay finds nothing.
    assert (await transact(repository, lambda uow: uow.accounts.consume_merge_ticket("CODE1234"), commit=True)) is None


async def test_the_plaintext_code_is_never_stored(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _account(repository, 1)
    assert account.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(account.id, "CODE1234", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )

    async with async_session_factory() as session:
        digests = (await session.scalars(select(AccountMergeTicketModel.code_digest))).all()

    assert list(digests) != ["CODE1234"]
    assert all("CODE1234" not in digest for digest in digests)


async def test_minting_replaces_the_accounts_live_ticket(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _account(repository, 1)
    assert account.id is not None

    await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(account.id, "FIRST", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )
    await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(account.id, "SECOND", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )

    assert (await transact(repository, lambda uow: uow.accounts.peek_merge_ticket("FIRST"))) is None
    assert (await transact(repository, lambda uow: uow.accounts.peek_merge_ticket("SECOND"))) is not None
    async with async_session_factory() as session:
        assert len((await session.scalars(select(AccountMergeTicketModel.account_id))).all()) == 1


async def test_a_ticket_cannot_be_minted_already_expired(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    """The CHECK is what stops a bad TTL from writing a ticket nobody could ever redeem."""
    account = await _account(repository, 1)
    assert account.id is not None

    with pytest.raises(IntegrityError):
        await transact(
            repository, lambda uow: uow.accounts.replace_merge_ticket(account.id, "STALE", ttl_seconds=-1), commit=True
        )


async def test_an_expired_ticket_stops_matching(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Expired tickets are ignored even while their rows remain in storage."""
    account = await _account(repository, 1)
    assert account.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(account.id, "STALE", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )

    # Age the whole row rather than only its expiry, so the CHECK stays satisfied.
    past = Instant.now().subtract(hours=1)
    async with async_session_factory.begin() as session:
        await session.execute(
            update(AccountMergeTicketModel)
            .where(AccountMergeTicketModel.account_id == account.id)
            .values(created_at=past, expires_at=past.add(seconds=MERGE_TICKET_TTL_SECONDS))
        )

    assert (await transact(repository, lambda uow: uow.accounts.peek_merge_ticket("STALE"))) is None
    assert (await transact(repository, lambda uow: uow.accounts.consume_merge_ticket("STALE"), commit=True)) is None


async def test_a_ticket_needs_a_real_account(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    with pytest.raises(AccountNotFoundError):
        await transact(
            repository,
            lambda uow: uow.accounts.replace_merge_ticket(9999, "CODE", MERGE_TICKET_TTL_SECONDS),
            commit=True,
        )


async def test_deleting_an_account_takes_its_ticket(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A merged-away account must not leave a redeemable ticket behind."""
    survivor = await _account(repository, 1)
    absorbed = await _account(repository, 2)
    assert survivor.id is not None
    assert absorbed.id is not None
    await transact(
        repository,
        lambda uow: uow.accounts.replace_merge_ticket(absorbed.id, "ORPHAN", MERGE_TICKET_TTL_SECONDS),
        commit=True,
    )

    await transact(repository, lambda uow: uow.accounts.merge(survivor.id, absorbed.id), commit=True)

    assert (await transact(repository, lambda uow: uow.accounts.peek_merge_ticket("ORPHAN"))) is None
    async with async_session_factory() as session:
        assert (await session.scalars(select(AccountMergeTicketModel.account_id))).all() == []


async def test_failed_merge_restores_the_ticket_and_account_resources(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork], monkeypatch: pytest.MonkeyPatch
) -> None:
    survivor = await _account(repository, 501)
    absorbed = await _account(repository, 502)

    async def unused_lookup(_uuid: object) -> str | None:
        return None

    service = AccountService(unused_lookup, lambda: 123456, uow_factory=repository)
    code, _ticket = await service.create_merge_code(absorbed.id)
    merge = AccountRepository.merge

    async def fail_after_merge(self: AccountRepository, surviving_id: int, absorbed_id: int) -> AccountMerge:
        await merge(self, surviving_id, absorbed_id)
        raise RuntimeError("fail after transferring account resources")

    with monkeypatch.context() as patch:
        patch.setattr(AccountRepository, "merge", fail_after_merge)
        with pytest.raises(RuntimeError, match="transferring account resources"):
            await service.complete_merge(survivor.id, code)

    assert await service.get_account_by_id(absorbed.id) is not None
    assert await transact(repository, lambda uow: uow.accounts.peek_merge_ticket(code)) is not None
    await service.complete_merge(survivor.id, code)
    assert await service.get_account_by_id(absorbed.id) is None
    assert await transact(repository, lambda uow: uow.accounts.peek_merge_ticket(code)) is None
