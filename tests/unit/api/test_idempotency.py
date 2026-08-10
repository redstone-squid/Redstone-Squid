"""HTTP idempotency contract tests."""

from typing import override
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from whenever import Instant

from squid.core.errors import ErrorCode
from squid.idempotency import IdempotencyService, PendingRequest, StoredResponse
from squid.idempotency.application import IdempotencyRepository
from squid.idempotency.domain import ExistingRequest, IdempotencyInProgressError, Reservation
from tests.unit.api.fakes import TEST_SYNERGY_SECRET, TEST_UUID, MockDatabaseManager, build_app


class MemoryIdempotencyRepository(IdempotencyRepository):
    """Deterministic repository fake preserving the production key semantics."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], ExistingRequest] = {}
        self.pending: dict[object, tuple[str, str]] = {}

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
        del method, route, expires_at, now
        identity = (principal, key)
        if existing := self.records.get(identity):
            return existing
        request = PendingRequest(uuid4())
        self.records[identity] = ExistingRequest(fingerprint, None)
        self.pending[request.request_id] = identity
        return request

    @override
    async def complete(self, request: PendingRequest, response: StoredResponse, *, now: Instant) -> None:
        del now
        identity = self.pending.pop(request.request_id)
        existing = self.records[identity]
        self.records[identity] = ExistingRequest(existing.fingerprint, response)

    @override
    async def purge_expired(self, *, now: Instant) -> int:
        del now
        return 0


class CountingAccounts:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_verification_code(self, _minecraft_uuid: UUID) -> int:
        self.calls += 1
        return 100_000 + self.calls


def idempotent_client() -> tuple[TestClient, CountingAccounts, MockDatabaseManager]:
    accounts = CountingAccounts()
    service = IdempotencyService(MemoryIdempotencyRepository())
    app, database = build_app(idempotency=service, accounts=accounts)
    return TestClient(app), accounts, database


def test_replays_completed_response_without_repeating_mutation() -> None:
    client, accounts, database = idempotent_client()
    headers = {"Authorization": TEST_SYNERGY_SECRET, "Idempotency-Key": "verification-request-1"}

    with client:
        first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)
        replay = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)

    assert database.closed
    assert first.status_code == replay.status_code == 201
    assert first.content == replay.content == b"100001"
    assert first.headers["content-type"] == replay.headers["content-type"]
    assert accounts.calls == 1


def test_reusing_key_for_different_payload_returns_conflict() -> None:
    client, accounts, database = idempotent_client()
    headers = {"Authorization": TEST_SYNERGY_SECRET, "Idempotency-Key": "verification-request-2"}

    with client:
        first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)
        conflict = client.post(
            "/v1/verify",
            json={"uuid": "22222222-2222-2222-2222-222222222222"},
            headers=headers,
        )

    assert database.closed
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_CONFLICT
    assert accounts.calls == 1


def test_request_without_key_retains_normal_non_idempotent_behavior() -> None:
    client, accounts, database = idempotent_client()
    headers = {"Authorization": TEST_SYNERGY_SECRET}

    with client:
        first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)
        second = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)

    assert database.closed
    assert first.json() == 100_001
    assert second.json() == 100_002
    assert accounts.calls == 2


@pytest.mark.asyncio
async def test_pending_key_blocks_concurrent_duplicate_but_is_scoped_per_principal() -> None:
    service = IdempotencyService(MemoryIdempotencyRepository())

    async def reserve_for(principal: str) -> PendingRequest | StoredResponse:
        return await service.reserve(
            principal=principal,
            key="concurrent-request",
            fingerprint=b"same-request",
            method="POST",
            route="/v1/builds",
        )

    first = await reserve_for("account:1")
    with pytest.raises(IdempotencyInProgressError):
        await reserve_for("account:1")
    other_principal = await reserve_for("account:2")

    assert isinstance(first, PendingRequest)
    assert isinstance(other_principal, PendingRequest)
    assert first.request_id != other_principal.request_id
