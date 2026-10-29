"""HTTP idempotency contract tests."""

import asyncio
import json
from collections.abc import Iterator
from typing import cast, override
from uuid import UUID, uuid4

import pytest
from anyio import create_task_group
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
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
from tests.unit.api.fakes import (
    TEST_CONFIG,
    TEST_SYNERGY_SECRET,
    TEST_UUID,
    MockAccountManager,
    build_app,
)


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


class CountingAccounts(MockAccountManager):
    """Counts verification codes issued, to prove a replay never mints a second one."""

    def __init__(self) -> None:
        self.calls = 0

    @override
    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
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
    *,
    sent: list[Message] | None = None,
) -> list[Message]:
    if sent is None:
        sent = []
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


@pytest.fixture
def idempotent_client() -> Iterator[tuple[TestClient, CountingAccounts]]:
    accounts = CountingAccounts()
    service = IdempotencyService(MemoryIdempotencyRepository())
    app, database = build_app(idempotency=service, accounts=accounts)
    with TestClient(app, headers={"Authorization": TEST_SYNERGY_SECRET}) as client:
        yield client, accounts
    assert database.closed


def test_replays_completed_response_without_repeating_mutation(
    idempotent_client: tuple[TestClient, CountingAccounts],
) -> None:
    client, accounts = idempotent_client
    headers = {"Idempotency-Key": "verification-request"}

    first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)
    replay = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)

    assert first.status_code == replay.status_code == 201
    assert first.content == replay.content == b"100001"
    assert first.headers["content-type"] == replay.headers["content-type"]
    assert accounts.calls == 1


def test_reusing_key_for_different_payload_returns_conflict(
    idempotent_client: tuple[TestClient, CountingAccounts],
) -> None:
    client, accounts = idempotent_client
    headers = {"Idempotency-Key": "verification-request"}

    first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)
    conflict = client.post(
        "/v1/verify",
        json={"uuid": "22222222-2222-2222-2222-222222222222"},
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_CONFLICT
    assert accounts.calls == 1


def test_resource_path_is_part_of_the_request_fingerprint() -> None:
    service = IdempotencyService(MemoryIdempotencyRepository())
    app, _database = build_app(idempotency=service)
    mutations: list[int] = []

    @app.post("/mutation/{resource_id}", dependencies=[Depends(reserve_idempotent_request)])
    async def mutate(resource_id: int) -> dict[str, int]:
        mutations.append(resource_id)
        return {"resource_id": resource_id}

    headers = {"Authorization": TEST_SYNERGY_SECRET, "Idempotency-Key": "resource-mutation"}
    with TestClient(app) as client:
        first = client.post("/mutation/1", headers=headers)
        replay = client.post("/mutation/1", headers=headers)
        conflict = client.post("/mutation/2", headers=headers)

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"resource_id": 1}
    assert conflict.status_code == 409
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_CONFLICT
    assert mutations == [1]


@pytest.mark.parametrize(
    ("retry_url", "retry_content_type"),
    [
        pytest.param("/v1/verify?source=two", "application/json", id="query"),
        pytest.param("/v1/verify?source=one", "application/json; charset=utf-8", id="content-type"),
    ],
)
def test_reusing_key_for_different_request_metadata_returns_conflict(
    idempotent_client: tuple[TestClient, CountingAccounts], retry_url: str, retry_content_type: str
) -> None:
    client, accounts = idempotent_client
    body = json.dumps({"uuid": str(TEST_UUID)}, separators=(",", ":"))
    headers = {"Idempotency-Key": "verification-request", "Content-Type": "application/json"}

    first = client.post("/v1/verify?source=one", content=body, headers=headers)
    conflict = client.post(retry_url, content=body, headers={**headers, "Content-Type": retry_content_type})

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_CONFLICT
    assert accounts.calls == 1


def test_request_without_key_retains_normal_non_idempotent_behavior(
    idempotent_client: tuple[TestClient, CountingAccounts],
) -> None:
    client, accounts = idempotent_client

    first = client.post("/v1/verify", json={"uuid": str(TEST_UUID)})
    second = client.post("/v1/verify", json={"uuid": str(TEST_UUID)})

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
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
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
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    recorder = CompletionRecorder(RuntimeError("database unavailable"))
    sent: list[Message] = []
    with pytest.raises(RuntimeError, match="database unavailable"):
        await run_completion_middleware(app, recorder, sent=sent)

    assert sent == []


@pytest.mark.asyncio
async def test_cancellation_after_handler_success_does_not_interrupt_completion() -> None:
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
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
    async def app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        raise RuntimeError("handler failed")

    recorder = CompletionRecorder()
    sent: list[Message] = []
    with pytest.raises(RuntimeError, match="handler failed"):
        await run_completion_middleware(app, recorder, sent=sent)

    assert recorder.responses == []
    assert sent == []


@pytest.mark.asyncio
async def test_response_body_limit_is_enforced_before_bytes_are_sent() -> None:
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b"x" * (MAX_IDEMPOTENT_RESPONSE_BYTES + 1),
                "more_body": False,
            }
        )

    recorder = CompletionRecorder()
    sent: list[Message] = []
    with pytest.raises(RuntimeError, match="replay limit"):
        await run_completion_middleware(app, recorder, sent=sent)

    assert recorder.responses == []
    assert sent == []


@pytest.mark.asyncio
async def test_response_body_limit_stops_capturing_at_the_first_oversized_chunk() -> None:
    frames_sent = 0

    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        nonlocal frames_sent
        frames_sent += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        frames_sent += 1
        await send(
            {
                "type": "http.response.body",
                "body": b"x" * MAX_IDEMPOTENT_RESPONSE_BYTES,
                "more_body": True,
            }
        )
        frames_sent += 1
        await send({"type": "http.response.body", "body": b"x", "more_body": True})
        frames_sent += 1
        await send({"type": "http.response.body", "body": b"never buffered", "more_body": False})

    with pytest.raises(RuntimeError, match="replay limit"):
        await run_completion_middleware(app, CompletionRecorder())

    assert frames_sent == 3


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
