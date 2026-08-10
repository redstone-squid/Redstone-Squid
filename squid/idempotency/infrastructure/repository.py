"""PostgreSQL idempotency repository."""

from typing import Any, cast
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.idempotency.application import IdempotencyRepository
from squid.idempotency.domain import ExistingRequest, PendingRequest, Reservation, StoredResponse
from squid.idempotency.infrastructure.models import IdempotencyRequest


class PostgresIdempotencyRepository(IdempotencyRepository):
    """Atomically reserve keys across every API process."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def reserve(
        self,
        *,
        principal: str,
        key: str,
        fingerprint: bytes,
        method: str,
        route: str,
        expires_at: Instant,
        now: Instant,
    ) -> Reservation:
        candidate_id = uuid4()
        async with self._session_factory.begin() as session:
            await session.execute(delete(IdempotencyRequest).where(IdempotencyRequest.expires_at <= now))
            request_id = await session.scalar(
                insert(IdempotencyRequest)
                .values(
                    id=candidate_id,
                    principal=principal,
                    idempotency_key=key,
                    request_fingerprint=fingerprint,
                    method=method,
                    route=route,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[IdempotencyRequest.principal, IdempotencyRequest.idempotency_key]
                )
                .returning(IdempotencyRequest.id)
            )
            if request_id is not None:
                return PendingRequest(request_id)
            existing = await session.scalar(
                select(IdempotencyRequest).where(
                    IdempotencyRequest.principal == principal,
                    IdempotencyRequest.idempotency_key == key,
                )
            )
        assert existing is not None
        response = None
        if existing.state == "completed":
            assert existing.response_status is not None
            assert existing.response_headers is not None
            assert existing.response_body is not None
            response = StoredResponse(
                status_code=existing.response_status,
                headers=tuple(existing.response_headers.items()),
                body=bytes(existing.response_body),
            )
        return ExistingRequest(bytes(existing.request_fingerprint), response)

    async def complete(self, request: PendingRequest, response: StoredResponse, *, now: Instant) -> None:
        headers = dict(response.headers)
        async with self._session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(IdempotencyRequest)
                    .where(IdempotencyRequest.id == request.request_id, IdempotencyRequest.state == "in_progress")
                    .values(
                        state="completed",
                        response_status=response.status_code,
                        response_headers=headers,
                        response_body=response.body,
                        completed_at=now,
                    )
                ),
            )
        if result.rowcount != 1:
            msg = f"Idempotency request {request.request_id} was not pending."
            raise RuntimeError(msg)
