"""PostgreSQL coverage for CLI device and session fencing."""

from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION
from squid.accounts.infrastructure.models import Account
from squid.accounts.infrastructure.repository import AccountRepository
from squid.cli_auth import CliAuthorizationService, CliSecretCodec
from squid.cli_auth.application import enrollment_proof_message, session_proof_message
from squid.cli_auth.errors import CliEnrollmentAlreadyExchangedError, InvalidCliSessionError
from squid.cli_auth.models import (
    CliDeviceEnrollmentRecord,
    CliDeviceRecord,
    CliSessionChallengeRecord,
    CliSessionRecord,
)
from squid.cli_auth.repository import PostgresCliAuthorizationRepository
from squid.persistence.base import Base

pytestmark = pytest.mark.asyncio

NOW = Instant.parse_iso("2026-08-11T22:00:00Z")
CLIENT_INSTANCE_ID = UUID("cafecafe-cafe-4afe-8afe-cafecafecafe")
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    cast(Table, CliDeviceEnrollmentRecord.__table__),
    cast(Table, CliDeviceRecord.__table__),
    cast(Table, CliSessionChallengeRecord.__table__),
    cast(Table, CliSessionRecord.__table__),
)


@pytest.fixture(autouse=True)
async def cli_auth_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=_TABLES)


async def consenting_account(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory.begin() as session:
        account = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=NOW)
        session.add(account)
        await session.flush()
        return account.id


def service(session_factory: async_sessionmaker[AsyncSession]) -> CliAuthorizationService:
    return CliAuthorizationService(
        PostgresCliAuthorizationRepository(session_factory),
        AccountRepository(session_factory, "unused-verification-code-pepper"),
        CliSecretCodec(b"integration-cli-auth-pepper-32-bytes"),
        now=lambda: NOW,
    )


def key_pair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, public_key


async def enroll(
    authorization: CliAuthorizationService,
    account_id: int,
) -> tuple[Ed25519PrivateKey, str]:
    private_key, public_key = key_pair()
    enrollment = await authorization.start_enrollment(
        public_key=public_key,
        client_instance_id=CLIENT_INSTANCE_ID,
        label="Integration workstation",
    )
    await authorization.approve_enrollment(user_code=enrollment.user_code, account_id=account_id)
    signature = private_key.sign(enrollment_proof_message(enrollment.enrollment.id, enrollment.device_code))
    issued = await authorization.exchange_enrollment(device_code=enrollment.device_code, signature=signature)
    return private_key, issued.token


async def test_enrollment_secrets_are_hash_only_and_exchange_is_one_time(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    authorization = service(async_session_factory)
    private_key, public_key = key_pair()
    enrollment = await authorization.start_enrollment(
        public_key=public_key,
        client_instance_id=CLIENT_INSTANCE_ID,
        label="Integration workstation",
    )
    await authorization.approve_enrollment(user_code=enrollment.user_code, account_id=account_id)
    signature = private_key.sign(enrollment_proof_message(enrollment.enrollment.id, enrollment.device_code))

    issued = await authorization.exchange_enrollment(device_code=enrollment.device_code, signature=signature)

    assert (await authorization.authenticate(issued.token)).account_id == account_id
    with pytest.raises(CliEnrollmentAlreadyExchangedError):
        await authorization.exchange_enrollment(device_code=enrollment.device_code, signature=signature)
    async with async_session_factory() as session:
        enrollment_record = await session.get(CliDeviceEnrollmentRecord, enrollment.enrollment.id)
        session_record = await session.get(CliSessionRecord, issued.session.id)
    assert enrollment_record is not None
    assert session_record is not None
    persisted = enrollment_record.device_code_hash + enrollment_record.user_code_hash + session_record.token_hash
    assert enrollment.device_code.encode() not in persisted
    assert enrollment.user_code.replace("-", "").encode() not in persisted
    assert issued.token.encode() not in persisted


async def test_signed_nonce_renews_session_and_device_revocation_fences_both(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await consenting_account(async_session_factory)
    authorization = service(async_session_factory)
    private_key, first_token = await enroll(authorization, account_id)
    identity = await authorization.authenticate(first_token)
    challenge = await authorization.start_session_challenge(identity.device_id)
    signature = private_key.sign(session_proof_message(identity.device_id, challenge.challenge.id, challenge.nonce))
    renewed = await authorization.exchange_session_challenge(
        device_id=identity.device_id,
        challenge_id=challenge.challenge.id,
        nonce=challenge.nonce,
        signature=signature,
    )

    assert (await authorization.authenticate(renewed.token)).device_id == identity.device_id
    assert await authorization.revoke_device(device_id=identity.device_id, account_id=account_id)
    with pytest.raises(InvalidCliSessionError):
        await authorization.authenticate(first_token)
    with pytest.raises(InvalidCliSessionError):
        await authorization.authenticate(renewed.token)
