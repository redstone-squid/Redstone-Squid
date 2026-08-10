"""PostgreSQL idempotency reservation integration tests."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.idempotency.domain import ExistingRequest, PendingRequest, Reservation, StoredResponse
from squid.idempotency.infrastructure.crypto import IdempotencyCiphertextError, IdempotencyResponseCipher
from squid.idempotency.infrastructure.models import IdempotencyRequest
from squid.idempotency.infrastructure.repository import PostgresIdempotencyRepository
from squid.persistence.base import Base

_TABLE = Base.metadata.tables["idempotency_requests"]
_OLD_KEY = b"o" * 32
_NEW_KEY = b"n" * 32


def encrypted_repository(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    active_key_id: str = "old",
    keys: dict[str, bytes] | None = None,
) -> PostgresIdempotencyRepository:
    return PostgresIdempotencyRepository(
        session_factory,
        IdempotencyResponseCipher(active_key_id=active_key_id, keys=keys or {"old": _OLD_KEY}),
    )


@pytest.fixture
async def idempotency_table(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(_TABLE.create)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(_TABLE.drop)


async def reserve(
    repository: PostgresIdempotencyRepository,
    *,
    now: Instant,
    expires_at: Instant | None = None,
    key: str = "build-submission-1",
) -> Reservation:
    return await repository.reserve(
        principal="user:1",
        key=key,
        fingerprint=b"request-fingerprint",
        method="POST",
        route="/v1/builds",
        expires_at=expires_at or now.add(hours=24),
        now=now,
    )


@pytest.mark.asyncio
async def test_completed_request_is_replayed(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = encrypted_repository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")

    pending = await reserve(repository, now=now)
    assert isinstance(pending, PendingRequest)
    response = StoredResponse(
        201,
        (
            ("content-type", "application/json"),
            ("cache-control", "no-store"),
            ("pragma", "no-cache"),
        ),
        b'{"secret":"one-time-credential"}',
    )
    await repository.complete(pending, response, now=now.add(seconds=1))

    async with async_session_factory() as session:
        stored = await session.scalar(select(IdempotencyRequest))
    assert stored is not None
    assert stored.response_body_key_id == "old"
    assert stored.response_body_ciphertext is not None
    assert response.body not in bytes(stored.response_body_ciphertext)

    replay = await reserve(repository, now=now.add(seconds=2))

    assert isinstance(replay, ExistingRequest)
    assert replay.fingerprint == b"request-fingerprint"
    assert replay.response is not None
    assert replay.response.status_code == response.status_code
    assert dict(replay.response.headers) == dict(response.headers)
    assert replay.response.body == response.body


@pytest.mark.asyncio
async def test_concurrent_reservations_have_one_winner(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = encrypted_repository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")

    reservations = await asyncio.gather(
        reserve(repository, now=now),
        reserve(repository, now=now),
    )

    assert sum(isinstance(item, PendingRequest) for item in reservations) == 1
    assert sum(isinstance(item, ExistingRequest) and item.response is None for item in reservations) == 1


@pytest.mark.asyncio
async def test_expired_key_can_be_reserved_again(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = encrypted_repository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")
    first = await reserve(repository, now=now, expires_at=now.add(seconds=1))
    replacement = await reserve(repository, now=now.add(seconds=2))

    assert isinstance(first, PendingRequest)
    assert isinstance(replacement, PendingRequest)
    assert first.request_id != replacement.request_id


@pytest.mark.asyncio
async def test_rotated_keyring_replays_responses_written_with_retained_key(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = Instant.parse_iso("2026-08-10T10:00:00Z")
    old_repository = encrypted_repository(async_session_factory)
    pending = await reserve(old_repository, now=now)
    assert isinstance(pending, PendingRequest)
    response = StoredResponse(201, (("cache-control", "no-store"),), b"credential")
    await old_repository.complete(pending, response, now=now.add(seconds=1))

    rotated_repository = encrypted_repository(
        async_session_factory,
        active_key_id="new",
        keys={"new": _NEW_KEY, "old": _OLD_KEY},
    )
    replay = await reserve(rotated_repository, now=now.add(seconds=2))

    assert isinstance(replay, ExistingRequest)
    assert replay.response == response


@pytest.mark.asyncio
async def test_ciphertext_swapped_between_records_fails_authentication(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = encrypted_repository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")
    first = await reserve(repository, now=now, key="first-request")
    second = await reserve(repository, now=now, key="second-request")
    assert isinstance(first, PendingRequest)
    assert isinstance(second, PendingRequest)
    await repository.complete(first, StoredResponse(200, (("etag", "first"),), b"first"), now=now)
    await repository.complete(second, StoredResponse(200, (("etag", "second"),), b"second"), now=now)

    async with async_session_factory.begin() as session:
        second_record = await session.scalar(
            select(IdempotencyRequest).where(IdempotencyRequest.id == second.request_id)
        )
        assert second_record is not None
        await session.execute(
            update(IdempotencyRequest)
            .where(IdempotencyRequest.id == first.request_id)
            .values(
                response_body_ciphertext=second_record.response_body_ciphertext,
                response_body_key_id=second_record.response_body_key_id,
                response_body_nonce=second_record.response_body_nonce,
            )
        )

    with pytest.raises(IdempotencyCiphertextError):
        await reserve(repository, now=now.add(seconds=1), key="first-request")


@pytest.mark.asyncio
async def test_explicit_purge_removes_expired_rows_without_api_traffic(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = encrypted_repository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")
    await reserve(repository, now=now, expires_at=now.add(seconds=1))

    assert await repository.purge_expired(now=now.add(seconds=2)) == 1
    async with async_session_factory() as session:
        assert await session.scalar(select(IdempotencyRequest)) is None
