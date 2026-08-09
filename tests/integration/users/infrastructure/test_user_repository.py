"""Integration coverage for account identity and creator alias claiming."""

import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.persistence.base import Base
from squid.users.domain import CONSENT_CUTOFF, CURRENT_CONSENT_VERSION, ClaimMethod, ClaimStatus, UserConsent
from squid.users.errors import AliasAlreadyClaimedError, CreatorAliasNotFoundError
from squid.users.infrastructure.models import CreatorAlias, CreatorAliasClaim, User, VerificationCode
from squid.users.infrastructure.repository import UserRepository

CONSENT = UserConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")
BEFORE_CUTOFF = Instant.parse_iso(CONSENT_CUTOFF).subtract(hours=1)
AFTER_CUTOFF = Instant.parse_iso(CONSENT_CUTOFF).add(hours=1)

_TABLES = [
    Base.metadata.tables["users"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
]


@pytest.fixture
async def user_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    """Create the account tables from the models, then drop them again."""
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.fixture
def repository(user_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]) -> UserRepository:
    return UserRepository(async_session_factory, "pepper")


async def _add_alias(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with session_factory() as session:
        alias = CreatorAlias(name=name)
        session.add(alias)
        await session.commit()
        return alias.id


async def test_normalized_name_collapses_case_variants(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`Foo` and `foo` are the same Minecraft account, so they share one credit."""
    await _add_alias(async_session_factory, "  Foo  ")

    async with async_session_factory() as session:
        session.add(CreatorAlias(name="foo"))
        with pytest.raises(IntegrityError):
            await session.commit()

    alias = await repository.get_alias_by_name("FOO")
    assert alias is not None
    assert alias.name == "  Foo  "


async def test_duplicate_discord_id_is_rejected(
    user_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with async_session_factory() as session:
        session.add(User(discord_id=1))
        await session.commit()
    async with async_session_factory() as session:
        session.add(User(discord_id=1))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_minecraft_link_without_a_receipt_is_rejected(
    user_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The consent receipt covers the Minecraft link, so it cannot be skipped."""
    async with async_session_factory() as session:
        session.add(User(discord_id=1, minecraft_uuid=MINECRAFT_UUID, created_at=AFTER_CUTOFF))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_minecraft_link_predating_the_notice_is_grandfathered(
    user_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Accounts linked before the notice existed keep their link and are re-prompted."""
    async with async_session_factory() as session:
        session.add(User(discord_id=1, minecraft_uuid=MINECRAFT_UUID, created_at=BEFORE_CUTOFF))
        await session.commit()
        assert await session.scalar(select(User.id).where(User.discord_id == 1)) is not None


async def test_submitter_only_row_needs_no_receipt(
    user_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with async_session_factory() as session:
        session.add(User(discord_id=1))
        await session.commit()
        assert await session.scalar(select(User.id).where(User.discord_id == 1)) is not None


async def test_claim_unclaimed_alias_matches_ignoring_case(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    account = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="Player")
    alias_id = await _add_alias(async_session_factory, "player")
    assert account.id is not None

    claimed = await repository.claim_unclaimed_alias(user_id=account.id, name="PLAYER", method=ClaimMethod.VERIFIED_IGN)

    assert claimed is not None
    assert claimed.id == alias_id
    assert claimed.user_id == account.id
    assert claimed.claim_method == ClaimMethod.VERIFIED_IGN


async def test_concurrent_claims_leave_exactly_one_winner(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The `user_id IS NULL` predicate lives in the UPDATE, so nobody is overwritten."""
    first = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="Player")
    second = await repository.add(consent=CONSENT, discord_id=2, ign="Player")
    await _add_alias(async_session_factory, "Player")
    assert first.id is not None
    assert second.id is not None

    results = await asyncio.gather(
        repository.claim_unclaimed_alias(user_id=first.id, name="Player", method=ClaimMethod.VERIFIED_IGN),
        repository.claim_unclaimed_alias(user_id=second.id, name="Player", method=ClaimMethod.VERIFIED_IGN),
    )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].user_id in {first.id, second.id}


async def test_verification_code_is_consumed_by_exactly_one_link(
    repository: UserRepository,
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
        assert len((await session.scalars(select(User).where(User.minecraft_uuid == MINECRAFT_UUID))).all()) == 1


async def test_claim_request_and_approval_credits_the_claimant(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    account = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="NewName")
    alias_id = await _add_alias(async_session_factory, "OldName")
    assert account.id is not None

    claim = await repository.request_claim(name="oldname", user_id=account.id)
    assert claim.alias_name == "OldName"
    assert [pending.id for pending in await repository.pending_claims()] == [claim.id]

    resolved = await repository.resolve_claim(claim_id=claim.id, status=ClaimStatus.APPROVED, resolved_by_discord_id=7)

    assert resolved.status is ClaimStatus.APPROVED
    assert await repository.pending_claims() == []
    async with async_session_factory() as session:
        alias = await session.get(CreatorAlias, alias_id)
        assert alias is not None
        assert alias.user_id == account.id
        assert alias.claim_method == ClaimMethod.STAFF_APPROVED


async def test_repeated_claim_requests_reuse_the_pending_row(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The partial unique index would reject a second pending row for the same pair."""
    account = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="NewName")
    await _add_alias(async_session_factory, "OldName")
    assert account.id is not None

    first = await repository.request_claim(name="OldName", user_id=account.id)
    second = await repository.request_claim(name="OldName", user_id=account.id)

    assert first.id == second.id
    async with async_session_factory() as session:
        assert len((await session.scalars(select(CreatorAliasClaim))).all()) == 1


async def test_claiming_a_held_credit_is_rejected(
    repository: UserRepository, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    holder = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="Player")
    other = await repository.add(consent=CONSENT, discord_id=2, ign="Player")
    await _add_alias(async_session_factory, "Player")
    assert holder.id is not None
    assert other.id is not None
    await repository.claim_unclaimed_alias(user_id=holder.id, name="Player", method=ClaimMethod.VERIFIED_IGN)

    with pytest.raises(AliasAlreadyClaimedError):
        await repository.request_claim(name="Player", user_id=other.id)


async def test_claim_request_for_an_uncredited_name_is_rejected(repository: UserRepository) -> None:
    account = await repository.add(consent=CONSENT, discord_id=1, minecraft_uuid=MINECRAFT_UUID, ign="Player")
    assert account.id is not None

    with pytest.raises(CreatorAliasNotFoundError):
        await repository.request_claim(name="NeverCredited", user_id=account.id)
