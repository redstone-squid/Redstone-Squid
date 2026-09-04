"""PostgreSQL coverage for Minecraft credential and device-flow fencing."""

import base64
import hashlib
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

import pytest
from anyio import create_task_group
from sqlalchemy import Table, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.infrastructure.models import Account, AccountIdentity
from squid.accounts.infrastructure.repository import AccountRepository
from squid.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.minecraft_auth.application.services import InstallationCredentialService, PlayerAuthorizationService
from squid.minecraft_auth.domain import PublicServerProfile
from squid.minecraft_auth.errors import (
    ChallengeAlreadyExchangedError,
    InvalidInstallationCredentialError,
    InvalidPlayerTokenError,
    TooManyActiveChallengesError,
)
from squid.minecraft_auth.infrastructure.models import (
    PaperInstallationRecord,
    PlayerChallengeRecord,
    PlayerGrantRecord,
)
from squid.minecraft_auth.infrastructure.repository import PostgresMinecraftAuthorizationRepository
from squid.persistence.base import Base
from squid.submissions.infrastructure.sponsors import PaperSponsorResolver

pytestmark = pytest.mark.asyncio

NOW = Instant.parse_iso("2026-08-11T12:00:00Z")
JAVA_UUID = UUID("d8de679a-3de4-4cb9-9f11-c961c72a3531")
PKCE_VERIFIER = "correct-verifier-" + "a" * 27
PKCE_CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(PKCE_VERIFIER.encode()).digest()).rstrip(b"=").decode()
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    cast(Table, AccountIdentity.__table__),
    cast(Table, PaperInstallationRecord.__table__),
    cast(Table, PlayerChallengeRecord.__table__),
    cast(Table, PlayerGrantRecord.__table__),
)


@pytest.fixture(autouse=True)
async def minecraft_auth_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=_TABLES)


async def consenting_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    java_uuid: UUID = JAVA_UUID,
) -> int:
    async with session_factory.begin() as session:
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=NOW)
        session.add(account)
        await session.flush()
        session.add(
            AccountIdentity(
                account_id=account.id,
                provider=IdentityProvider.JAVA,
                subject=str(java_uuid),
                verified_at=NOW,
            )
        )
        await session.flush()
        return account.id


def services(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_active_challenges: int = 5,
) -> tuple[InstallationCredentialService, PlayerAuthorizationService]:
    repository = PostgresMinecraftAuthorizationRepository(session_factory)
    accounts = AccountRepository(session_factory, "unused-verification-code-pepper")
    codec = MinecraftSecretCodec("integration-test-pepper")
    return (
        InstallationCredentialService(repository, accounts, codec, clock=lambda: NOW),
        PlayerAuthorizationService(
            repository,
            accounts,
            codec,
            clock=lambda: NOW,
            max_active_challenges=max_active_challenges,
        ),
    )


async def test_fabric_codes_and_token_are_hash_only_and_exchange_is_one_time(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    _, players = services(async_session_factory)

    challenge = await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)
    await players.approve(user_code=challenge.user_code, account_id=account_id)
    issued = await players.exchange_fabric(device_code=challenge.device_code, pkce_verifier=PKCE_VERIFIER)

    context = await players.authenticate_fabric_player(issued.token)
    assert (context.account_id, context.java_uuid) == (account_id, JAVA_UUID)
    with pytest.raises(ChallengeAlreadyExchangedError):
        await players.exchange_fabric(device_code=challenge.device_code, pkce_verifier=PKCE_VERIFIER)

    async with async_session_factory() as session:
        challenge_record = await session.get(PlayerChallengeRecord, challenge.id)
        grant_record = await session.get(PlayerGrantRecord, issued.grant.id)
    assert challenge_record is not None
    assert grant_record is not None
    persisted = challenge_record.device_code_hash + challenge_record.user_code_hash + grant_record.token_hash
    assert challenge.device_code.encode() not in persisted
    assert challenge.user_code.replace("-", "").encode() not in persisted
    assert issued.token.encode() not in persisted


async def test_paper_rotation_atomically_fences_credential_challenge_and_grant(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    installations, players = services(async_session_factory)
    first = await installations.register(owner_account_id=account_id, label="Survival")
    await installations.update_profile(
        installation_id=first.installation.id,
        owner_account_id=account_id,
        profile=PublicServerProfile(enabled=True, display_name="Survival", sponsor_opt_in=True),
    )
    (published,) = await installations.public_servers()
    assert published.installation_id == first.installation.id
    authenticated = await installations.authenticate(first.token)
    challenge = await players.start_paper_challenge(installation=authenticated, java_uuid=JAVA_UUID)
    await players.approve(user_code=challenge.user_code, account_id=account_id)
    grant = await players.exchange_paper(device_code=challenge.device_code, installation=authenticated)

    replacement = await installations.rotate(installation_id=authenticated.id, owner_account_id=account_id)

    assert replacement.installation.id == authenticated.id
    assert replacement.installation.credential_version == authenticated.credential_version + 1
    (published_after_rotation,) = await installations.public_servers()
    assert published_after_rotation == published
    with pytest.raises(InvalidInstallationCredentialError):
        await installations.authenticate(first.token)
    with pytest.raises(InvalidPlayerTokenError):
        await players.authenticate_paper_player(grant.token, authenticated)
    async with async_session_factory() as session:
        stored_grant = await session.get(PlayerGrantRecord, grant.grant.id)
    assert stored_grant is not None
    assert stored_grant.revoked_at == NOW


async def test_targeted_public_sponsor_lookup_isolated_from_unrelated_malformed_profile(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    installations, _ = services(async_session_factory)
    valid = await installations.register(
        owner_account_id=account_id,
        label="Valid public server",
        profile=PublicServerProfile(
            enabled=True,
            display_name="Valid server",
            website_url="https://example.test/server",
            sponsor_opt_in=True,
        ),
    )
    listed_only = await installations.register(
        owner_account_id=account_id,
        label="Listed server without sponsor consent",
        profile=PublicServerProfile(enabled=True, display_name="Listed only"),
    )
    malformed = await installations.register(owner_account_id=account_id, label="Malformed public server")
    async with async_session_factory.begin() as session:
        await session.execute(
            update(PaperInstallationRecord)
            .where(PaperInstallationRecord.id == malformed.installation.id)
            .values(public_profile_enabled=True, public_display_name=" ", sponsor_opt_in=True)
        )

    sponsor = await PaperSponsorResolver(installations).resolve(valid.installation.id)

    assert await installations.get_public_server(listed_only.installation.id) is None
    assert sponsor is not None
    assert sponsor.installation_id == valid.installation.id
    assert sponsor.display_name == "Valid server"


async def test_postgres_advisory_fence_enforces_active_challenge_bound(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await consenting_account(async_session_factory)
    _, players = services(async_session_factory, max_active_challenges=1)
    outcomes: list[object] = []

    async def start_challenge() -> None:
        try:
            outcomes.append(
                await players.start_fabric_challenge(java_uuid=JAVA_UUID, pkce_s256_challenge=PKCE_CHALLENGE)
            )
        except TooManyActiveChallengesError as error:
            outcomes.append(error)

    async with create_task_group() as tasks:
        tasks.start_soon(start_challenge)
        tasks.start_soon(start_challenge)

    assert len([outcome for outcome in outcomes if isinstance(outcome, TooManyActiveChallengesError)]) == 1
    assert len([outcome for outcome in outcomes if not isinstance(outcome, TooManyActiveChallengesError)]) == 1


async def test_account_authorizer_requires_current_consent_and_exact_java_uuid(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    authorizer = AccountRepository(async_session_factory, "unused-verification-code-pepper")
    second_java_uuid = UUID("24d86f82-6193-4886-94af-8bc62994192d")
    async with async_session_factory.begin() as session:
        session.add(
            AccountIdentity(
                account_id=account_id,
                provider=IdentityProvider.JAVA,
                subject=str(second_java_uuid),
                verified_at=NOW,
            )
        )

    assert await authorizer.has_current_consent(account_id)
    assert await authorizer.can_approve_minecraft_identity(account_id=account_id, java_uuid=JAVA_UUID)
    assert await authorizer.can_approve_minecraft_identity(account_id=account_id, java_uuid=second_java_uuid)
    assert not await authorizer.can_approve_minecraft_identity(
        account_id=account_id,
        java_uuid=UUID("041873ab-65e9-4f44-a225-89d621df8e90"),
    )

    async with async_session_factory.begin() as session:
        account = await session.scalar(select(Account).where(Account.id == account_id))
        assert account is not None
        account.consent_version = "outdated"

    assert not await authorizer.has_current_consent(account_id)
    assert not await authorizer.can_approve_minecraft_identity(account_id=account_id, java_uuid=JAVA_UUID)
