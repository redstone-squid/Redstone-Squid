"""Transport-level request and private-response boundaries."""

from uuid import UUID

from httpx import ASGITransport, AsyncClient
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.middleware import DefineMiddleware

from squid.api.private_responses import PrivateResponseHeadersMiddleware
from squid.api.request_body import BoundedRequestBodyMiddleware, streams_own_body

_DRAFT_ID = UUID("64760b2f-b352-45e0-9ed1-67b9da901992")


async def test_oversized_body_is_rejected_before_a_handler_without_an_idempotency_key() -> None:
    calls = 0

    @post(status_code=200, path="/mutation")
    async def mutation(request: Request) -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"size": len(await request.body())}

    app = Litestar(route_handlers=[mutation], middleware=[DefineMiddleware(BoundedRequestBodyMiddleware, max_bytes=32)])
    # Litestar and httpx describe the same ASGI callable with different scope types.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:  # type: ignore[bad-argument-type]
        response = await client.post("/mutation", content=b"x" * 33, headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 413
    assert calls == 0


async def test_chunked_body_is_bounded_without_a_content_length() -> None:
    calls = 0

    @post(status_code=200, path="/mutation")
    async def mutation(request: Request) -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"size": len(await request.body())}

    async def content():
        yield b"x" * 20
        yield b"y" * 20

    app = Litestar(route_handlers=[mutation], middleware=[DefineMiddleware(BoundedRequestBodyMiddleware, max_bytes=32)])
    # Litestar and httpx describe the same ASGI callable with different scope types.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:  # type: ignore[bad-argument-type]
        response = await client.post("/mutation", content=content())

    assert response.status_code == 413
    assert calls == 0


async def test_bounded_media_upload_path_keeps_its_streaming_limit_owner() -> None:

    # This stub proves the global cap is bypassed; production upload handlers enforce their own cap.
    @post(status_code=200, path="/v1/submissions/drafts/{draft_id:uuid}/media/{kind:str}")
    @streams_own_body
    async def upload(draft_id: UUID, kind: str, request: Request) -> dict[str, object]:
        return {"draft_id": str(draft_id), "kind": kind, "size": len(await request.body())}

    app = Litestar(route_handlers=[upload], middleware=[DefineMiddleware(BoundedRequestBodyMiddleware, max_bytes=8)])
    # Litestar and httpx describe the same ASGI callable with different scope types.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:  # type: ignore[bad-argument-type]
        response = await client.post(
            f"/v1/submissions/drafts/{_DRAFT_ID}/media/image",
            content=b"bounded-by-the-media-route",
            headers={"Content-Type": "image/png"},
        )

    assert response.status_code == 200
    assert response.json()["size"] == len(b"bounded-by-the-media-route")


async def test_an_unmarked_route_on_the_same_prefix_is_still_bounded() -> None:
    """The exemption follows the endpoint, not the shape of its path."""

    @post(status_code=200, path="/v1/submissions/drafts/{draft_id:uuid}/media/{kind:str}")
    async def upload(draft_id: UUID, kind: str, request: Request) -> dict[str, object]:
        return {"draft_id": str(draft_id), "kind": kind, "size": len(await request.body())}

    app = Litestar(route_handlers=[upload], middleware=[DefineMiddleware(BoundedRequestBodyMiddleware, max_bytes=8)])
    # Litestar and httpx describe the same ASGI callable with different scope types.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:  # type: ignore[bad-argument-type]
        response = await client.post(
            f"/v1/submissions/drafts/{_DRAFT_ID}/media/image",
            content=b"bounded-by-the-media-route",
            headers={"Content-Type": "image/png"},
        )

    assert response.status_code == 413


async def test_private_paths_are_no_store_on_success_and_errors() -> None:

    @get("/v1/submissions/drafts/{draft_id:uuid}")
    async def private_draft(draft_id: UUID) -> dict[str, str]:
        return {"id": str(draft_id)}

    @get("/v1/users/me/failure")
    async def private_failure() -> None:
        raise HTTPException(status_code=404)

    @get("/v1/builds/1")
    async def public_build() -> dict[str, int]:
        return {"id": 1}

    app = Litestar(
        route_handlers=[private_draft, private_failure, public_build],
        middleware=[DefineMiddleware(PrivateResponseHeadersMiddleware)],
    )
    # Litestar and httpx describe the same ASGI callable with different scope types.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as client:  # type: ignore[bad-argument-type]
        private_response = await client.get(f"/v1/submissions/drafts/{_DRAFT_ID}")
        error_response = await client.get("/v1/users/me/failure")
        public_response = await client.get("/v1/builds/1")

    for response in (private_response, error_response):
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"
    assert "Cache-Control" not in public_response.headers
    assert "Pragma" not in public_response.headers
