"""PostgreSQL idempotency repository."""

from typing import Any, cast, override
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.idempotency.application import IdempotencyRepository
from squid.idempotency.domain import ExistingRequest, PendingRequest, Reservation, StoredResponse
from squid.idempotency.infrastructure.crypto import (
    EncryptedResponseBody,
    IdempotencyCiphertextError,
    IdempotencyEncryptionUnavailableError,
    IdempotencyResponseCipher,
    ResponseEncryptionMetadata,
)
from squid.idempotency.infrastructure.models import IdempotencyRequest


class PostgresIdempotencyRepository(IdempotencyRepository):
    """Atomically reserve keys across every API process."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: IdempotencyResponseCipher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    @override
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
        response = self._stored_response(existing) if existing.state == "completed" else None
        return ExistingRequest(bytes(existing.request_fingerprint), response)

    @override
    async def complete(self, request: PendingRequest, response: StoredResponse, *, now: Instant) -> None:
        headers = dict(response.headers)
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(IdempotencyRequest).where(IdempotencyRequest.id == request.request_id).with_for_update()
            )
            if existing is None or existing.state != "in_progress":
                msg = f"Idempotency request {request.request_id} was not pending."
                raise RuntimeError(msg)
            encrypted = self._cipher_or_raise().encrypt(
                response.body,
                self._metadata(existing, response.status_code, headers),
            )
            existing.state = "completed"
            existing.response_status = response.status_code
            existing.response_headers = headers
            existing.response_body_ciphertext = encrypted.ciphertext
            existing.response_body_key_id = encrypted.key_id
            existing.response_body_nonce = encrypted.nonce
            existing.completed_at = now

    @override
    async def purge_expired(self, *, now: Instant) -> int:
        """Delete expired reservations independently of incoming API traffic."""
        async with self._session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(IdempotencyRequest).where(IdempotencyRequest.expires_at <= now)),
            )
        return result.rowcount

    def _stored_response(self, request: IdempotencyRequest) -> StoredResponse:
        if (
            request.response_status is None
            or request.response_headers is None
            or request.response_body_ciphertext is None
            or request.response_body_key_id is None
            or request.response_body_nonce is None
        ):
            msg = "A completed idempotency response lacks authenticated ciphertext fields."
            raise IdempotencyCiphertextError(msg)
        headers = request.response_headers
        body = self._cipher_or_raise().decrypt(
            EncryptedResponseBody(
                key_id=request.response_body_key_id,
                nonce=bytes(request.response_body_nonce),
                ciphertext=bytes(request.response_body_ciphertext),
            ),
            self._metadata(request, request.response_status, headers),
        )
        return StoredResponse(
            status_code=request.response_status,
            headers=tuple(headers.items()),
            body=body,
        )

    def _cipher_or_raise(self) -> IdempotencyResponseCipher:
        if self._cipher is None:
            msg = "The idempotency repository was used for response storage without an encryption keyring."
            raise IdempotencyEncryptionUnavailableError(msg)
        return self._cipher

    @staticmethod
    def _metadata(
        request: IdempotencyRequest,
        status_code: int,
        headers: dict[str, str],
    ) -> ResponseEncryptionMetadata:
        return ResponseEncryptionMetadata(
            request_id=request.id,
            principal=request.principal,
            idempotency_key=request.idempotency_key,
            request_fingerprint=bytes(request.request_fingerprint),
            method=request.method,
            route=request.route,
            status_code=status_code,
            headers=headers,
        )
