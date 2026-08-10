"""PostgreSQL idempotency reservation integration tests."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.idempotency.domain import ExistingRequest, PendingRequest, Reservation, StoredResponse
from squid.idempotency.infrastructure.repository import PostgresIdempotencyRepository
from squid.persistence.base import Base

_TABLE = Base.metadata.tables["idempotency_requests"]


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
) -> Reservation:
    return await repository.reserve(
        principal="user:1",
        key="build-submission-1",
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
    repository = PostgresIdempotencyRepository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")

    pending = await reserve(repository, now=now)
    assert isinstance(pending, PendingRequest)
    response = StoredResponse(201, (("content-type", "application/json"),), b'{"id":42}')
    await repository.complete(pending, response, now=now.add(seconds=1))

    replay = await reserve(repository, now=now.add(seconds=2))

    assert isinstance(replay, ExistingRequest)
    assert replay.fingerprint == b"request-fingerprint"
    assert replay.response == response


@pytest.mark.asyncio
async def test_concurrent_reservations_have_one_winner(
    idempotency_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresIdempotencyRepository(async_session_factory)
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
    repository = PostgresIdempotencyRepository(async_session_factory)
    now = Instant.parse_iso("2026-08-10T10:00:00Z")
    first = await reserve(repository, now=now, expires_at=now.add(seconds=1))
    replacement = await reserve(repository, now=now.add(seconds=2))

    assert isinstance(first, PendingRequest)
    assert isinstance(replacement, PendingRequest)
    assert first.request_id != replacement.request_id
