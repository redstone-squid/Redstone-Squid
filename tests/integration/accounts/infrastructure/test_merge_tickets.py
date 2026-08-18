"""Integration coverage for the one-time codes that prove both sides of a merge."""

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    MERGE_TICKET_TTL_SECONDS,
    AccountConsent,
)
from squid.accounts.domain import (
    AccountIdentity as AccountIdentityValue,
)
from squid.accounts.errors import AccountNotFoundError
from squid.accounts.infrastructure.models import AccountMergeTicket as AccountMergeTicketModel
from squid.accounts.infrastructure.repository import AccountRepository

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Merging reaches across the schema, so this runs against the real migration chain."""
    return migrated_session_factory


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> AccountRepository:
    return AccountRepository(async_session_factory, "pepper")


async def _account(repository: AccountRepository, discord_id: int):
    return await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),))


async def test_a_ticket_round_trips_and_is_spent_once(repository: AccountRepository) -> None:
    account = await _account(repository, 1)
    assert account.id is not None

    ticket = await repository.replace_merge_ticket(account.id, "CODE1234", MERGE_TICKET_TTL_SECONDS)
    assert ticket.account_id == account.id
    assert ticket.expires_at > ticket.created_at

    assert (await repository.peek_merge_ticket("CODE1234")) is not None
    assert (await repository.consume_merge_ticket("CODE1234")) is not None
    # Spent: the delete is the redemption, so a replay finds nothing.
    assert (await repository.consume_merge_ticket("CODE1234")) is None


async def test_the_plaintext_code_is_never_stored(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _account(repository, 1)
    assert account.id is not None
    await repository.replace_merge_ticket(account.id, "CODE1234", MERGE_TICKET_TTL_SECONDS)

    async with async_session_factory() as session:
        digests = (await session.scalars(select(AccountMergeTicketModel.code_digest))).all()

    assert list(digests) != ["CODE1234"]
    assert all("CODE1234" not in digest for digest in digests)


async def test_minting_replaces_the_accounts_live_ticket(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account = await _account(repository, 1)
    assert account.id is not None

    await repository.replace_merge_ticket(account.id, "FIRST", MERGE_TICKET_TTL_SECONDS)
    await repository.replace_merge_ticket(account.id, "SECOND", MERGE_TICKET_TTL_SECONDS)

    assert (await repository.peek_merge_ticket("FIRST")) is None
    assert (await repository.peek_merge_ticket("SECOND")) is not None
    async with async_session_factory() as session:
        assert len((await session.scalars(select(AccountMergeTicketModel.account_id))).all()) == 1


async def test_a_ticket_cannot_be_minted_already_expired(repository: AccountRepository) -> None:
    """The CHECK is what stops a bad TTL from writing a ticket nobody could ever redeem."""
    account = await _account(repository, 1)
    assert account.id is not None

    with pytest.raises(IntegrityError):
        await repository.replace_merge_ticket(account.id, "STALE", ttl_seconds=-1)


async def test_an_expired_ticket_stops_matching(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Nothing reaps a lapsed ticket; it simply stops matching."""
    account = await _account(repository, 1)
    assert account.id is not None
    await repository.replace_merge_ticket(account.id, "STALE", MERGE_TICKET_TTL_SECONDS)

    # Age the whole row rather than only its expiry, so the CHECK stays satisfied.
    past = Instant.now().subtract(hours=1)
    async with async_session_factory.begin() as session:
        await session.execute(
            update(AccountMergeTicketModel)
            .where(AccountMergeTicketModel.account_id == account.id)
            .values(created_at=past, expires_at=past.add(seconds=MERGE_TICKET_TTL_SECONDS))
        )

    assert (await repository.peek_merge_ticket("STALE")) is None
    assert (await repository.consume_merge_ticket("STALE")) is None


async def test_a_ticket_needs_a_real_account(repository: AccountRepository) -> None:
    with pytest.raises(AccountNotFoundError):
        await repository.replace_merge_ticket(9999, "CODE", MERGE_TICKET_TTL_SECONDS)


async def test_deleting_an_account_takes_its_ticket(
    repository: AccountRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A merged-away account must not leave a redeemable ticket behind."""
    survivor = await _account(repository, 1)
    absorbed = await _account(repository, 2)
    assert survivor.id is not None
    assert absorbed.id is not None
    await repository.replace_merge_ticket(absorbed.id, "ORPHAN", MERGE_TICKET_TTL_SECONDS)

    await repository.merge(survivor.id, absorbed.id)

    assert (await repository.peek_merge_ticket("ORPHAN")) is None
    async with async_session_factory() as session:
        assert (await session.scalars(select(AccountMergeTicketModel.account_id))).all() == []
