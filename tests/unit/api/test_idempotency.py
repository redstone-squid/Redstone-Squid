"""HTTP idempotency contract tests."""

import asyncio
import json
from typing import cast, override
from uuid import UUID, uuid4

import pytest
from anyio import create_task_group
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp, Message, Scope
from whenever import Instant

from squid.api.idempotency import (
    MAX_IDEMPOTENT_RESPONSE_BYTES,
    CompleteIdempotentResponseMiddleware,
    IdempotencyReservationState,
    assert_idempotency_completion_installed,
    reserve_idempotent_request,
)
from squid.core.errors import ErrorCode
from squid.idempotency import IdempotencyService, PendingRequest, StoredResponse
from squid.idempotency.application import IdempotencyRepository
from squid.idempotency.domain import ExistingRequest, IdempotencyInProgressError, Reservation, UnsafeHttpMethod
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, TEST_UUID, MockDatabaseManager, build_app


class MemoryIdempotencyRepository(IdempotencyRepository):
    """Deterministic repository fake preserving the production key semantics."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], ExistingRequest] = {}
        self.pending: dict[object, tuple[str, str]] = {}

    @override
    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
        expires_at: Instant,
        now: Instant,
    ) -> Reservation:
        del method, route, expires_at, now
        identity = (caller, key)
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


class CompletionRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.responses: list[StoredResponse] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def complete(self, _request: PendingRequest, response: StoredResponse) -> None:
        self.started.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        self.responses.append(response)


async def run_completion_middleware(
    app: ASGIApp,
    recorder: CompletionRecorder,
) -> list[Message]:
    sent: list[Message] = []
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mutation",
            "raw_path": b"/mutation",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "state": {
                "squid_idempotency": IdempotencyReservationState(
                    cast(IdempotencyService, recorder),
                    PendingRequest(uuid4()),
                )
            },
        },
    )

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await CompleteIdempotentResponseMiddleware(app)(scope, receive, send)
    return sent


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


def test_query_and_content_type_are_part_of_the_request_fingerprint() -> None:
    client, accounts, database = idempotent_client()
    authorization = {"Authorization": TEST_SYNERGY_SECRET}
    body = json.dumps({"uuid": str(TEST_UUID)}, separators=(",", ":"))

    with client:
        first = client.post(
            "/v1/verify?source=one",
            content=body,
            headers={**authorization, "Idempotency-Key": "query", "Content-Type": "application/json"},
        )
        query_conflict = client.post(
            "/v1/verify?source=two",
            content=body,
            headers={**authorization, "Idempotency-Key": "query", "Content-Type": "application/json"},
        )
        media_conflict = client.post(
            "/v1/verify?source=one",
            content=body,
            headers={
                **authorization,
                "Idempotency-Key": "media-type",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        media_first = client.post(
            "/v1/verify?source=one",
            content=body,
            headers={**authorization, "Idempotency-Key": "media-type", "Content-Type": "application/json"},
        )

    assert database.closed
    assert first.status_code == 201
    assert query_conflict.status_code == 409
    assert media_conflict.status_code == 201
    assert media_first.status_code == 409
    assert accounts.calls == 2


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
async def test_pending_key_blocks_concurrent_duplicate_but_is_scoped_per_caller() -> None:
    service = IdempotencyService(MemoryIdempotencyRepository())

    async def reserve_for(caller: str) -> PendingRequest | StoredResponse:
        return await service.reserve(
            caller=caller,
            key="concurrent-request",
            fingerprint=b"same-request",
            method=UnsafeHttpMethod.POST,
            route="/v1/builds",
        )

    first = await reserve_for("account:1")
    with pytest.raises(IdempotencyInProgressError):
        await reserve_for("account:1")
    other_caller = await reserve_for("account:2")

    assert isinstance(first, PendingRequest)
    assert isinstance(other_caller, PendingRequest)
    assert first.request_id != other_caller.request_id


@pytest.mark.asyncio
async def test_completion_buffers_multiple_and_empty_body_frames_before_sending() -> None:
    async def app(_scope: Scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": [(b"etag", b'"one"')]})
        await send({"type": "http.response.body", "body": b"", "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    recorder = CompletionRecorder()
    sent = await run_completion_middleware(app, recorder)

    assert recorder.responses == [StoredResponse(204, (("etag", '"one"'),), b"")]
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]


@pytest.mark.asyncio
async def test_completion_failure_sends_no_partial_response() -> None:
    async def app(_scope: Scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    recorder = CompletionRecorder(RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await run_completion_middleware(app, recorder)


@pytest.mark.asyncio
async def test_cancellation_after_handler_success_does_not_interrupt_completion() -> None:
    async def app(_scope: Scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    recorder = CompletionRecorder()
    recorder.release.clear()
    async with create_task_group() as tasks:
        tasks.start_soon(run_completion_middleware, app, recorder)
        await recorder.started.wait()
        tasks.cancel_scope.cancel()
        recorder.release.set()

    assert recorder.responses == [StoredResponse(200, (), b"ok")]


@pytest.mark.asyncio
async def test_handler_failure_does_not_complete_or_send_a_response() -> None:
    async def app(_scope: Scope, _receive, _send) -> None:
        raise RuntimeError("handler failed")

    recorder = CompletionRecorder()
    with pytest.raises(RuntimeError, match="handler failed"):
        await run_completion_middleware(app, recorder)
    assert recorder.responses == []


@pytest.mark.asyncio
async def test_response_body_limit_is_enforced_before_bytes_are_sent() -> None:
    async def app(_scope: Scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b"x" * (MAX_IDEMPOTENT_RESPONSE_BYTES + 1),
                "more_body": False,
            }
        )

    recorder = CompletionRecorder()
    with pytest.raises(RuntimeError, match="replay limit"):
        await run_completion_middleware(app, recorder)
    assert recorder.responses == []


def test_configured_response_body_limit_is_applied() -> None:
    accounts = CountingAccounts()
    service = IdempotencyService(MemoryIdempotencyRepository())
    config = TEST_CONFIG.model_copy(
        update={"api": TEST_CONFIG.api.model_copy(update={"idempotency_max_response_bytes": 1})}
    )
    app, _database = build_app(idempotency=service, accounts=accounts, config=config)

    with TestClient(app) as client, pytest.raises(RuntimeError, match="1-byte replay limit"):
        client.post(
            "/v1/verify",
            json={"uuid": str(TEST_UUID)},
            headers={"Authorization": TEST_SYNERGY_SECRET, "Idempotency-Key": "small-cap"},
        )


def test_streaming_idempotent_route_is_rejected_at_startup() -> None:
    app = FastAPI()
    app.add_middleware(CompleteIdempotentResponseMiddleware)

    @app.post(
        "/stream",
        dependencies=[Depends(reserve_idempotent_request)],
        response_class=StreamingResponse,
    )
    async def stream() -> StreamingResponse:
        async def body():
            yield b"chunk"

        return StreamingResponse(body())

    with pytest.raises(TypeError, match="cannot declare a streaming response"):
        assert_idempotency_completion_installed(app)
