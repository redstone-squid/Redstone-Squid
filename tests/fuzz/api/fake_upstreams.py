"""Deterministic loopback-only Mojang and Discord services for API fuzzing."""

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from tests.fuzz.api.environment import CONTROL_NONCE_ENV, FAKE_PORT_ENV

CONTROL_HEADER = "X-Squid-Fuzz-Nonce"

MINECRAFT_ALICE = "00000000-0000-0000-0000-000000000101"
DISCORD_ALICE = 1001
DISCORD_GUILD = 9001
DISCORD_TRUSTED_ROLE = 9101
OAUTH_CODE_ALICE = "synthetic-code-alice"
OAUTH_TOKEN_ALICE = "synthetic-discord-access-alice"
MAX_OBSERVATIONS = 2_048
MAX_TOKEN_REQUEST_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class RequestObservation:
    """Redacted facts needed to prove credentials were not forwarded upstream."""

    method: str
    path: str
    authorization_scheme: str | None
    authorization_hash: str | None


class ObservationDocument(BaseModel):
    """JSON representation of one redacted upstream request observation."""

    method: str
    path: str
    authorization_scheme: str | None
    authorization_hash: str | None


class SnapshotDocument(BaseModel):
    """Bounded state returned only through the nonce-protected control route."""

    requests: list[ObservationDocument]


class FakeUpstreamState:
    """Small mutable state reset to the same synthetic baseline each time."""

    def __init__(self, control_nonce: str) -> None:
        if len(control_nonce) < 32:
            msg = "The fake-upstream control nonce must contain at least 32 characters."
            raise ValueError(msg)
        self._control_nonce = control_nonce
        self.requests: list[RequestObservation] = []

    def reset(self) -> None:
        """Restore all mutable fake-adapter state."""
        self.requests.clear()

    def authorize_control(self, supplied: str | None) -> bool:
        """Compare control credentials without timing-dependent early exits."""
        return supplied is not None and hmac.compare_digest(supplied, self._control_nonce)

    def observe(self, request: Request) -> None:
        """Record only a keyed digest of an upstream Authorization header."""
        if len(self.requests) >= MAX_OBSERVATIONS:
            return
        authorization = request.headers.get("authorization")
        scheme = authorization.partition(" ")[0] if authorization else None
        digest = (
            hmac.digest(self._control_nonce.encode(), authorization.encode(), hashlib.sha256).hex()
            if authorization
            else None
        )
        self.requests.append(RequestObservation(request.method, request.url.path, scheme, digest))


def create_fake_upstream_app(control_nonce: str) -> FastAPI:
    """Create deterministic fake upstreams with a nonce-protected control plane."""
    state = FakeUpstreamState(control_nonce)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.fake_upstreams = state

    @app.get("/__fuzz/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/__fuzz/reset", status_code=204)
    async def reset(
        nonce: Annotated[str | None, Header(alias=CONTROL_HEADER)] = None,
    ) -> None:
        if not state.authorize_control(nonce):
            raise HTTPException(status_code=404)
        state.reset()

    @app.get("/__fuzz/snapshot", response_model=SnapshotDocument)
    async def snapshot(
        nonce: Annotated[str | None, Header(alias=CONTROL_HEADER)] = None,
    ) -> SnapshotDocument:
        if not state.authorize_control(nonce):
            raise HTTPException(status_code=404)
        return SnapshotDocument(
            requests=[ObservationDocument.model_validate(item, from_attributes=True) for item in state.requests]
        )

    @app.get("/mojang/profile/{minecraft_uuid}")
    async def mojang_profile(minecraft_uuid: str, request: Request, response: Response) -> dict[str, str] | None:
        state.observe(request)
        if minecraft_uuid != MINECRAFT_ALICE:
            response.status_code = 204
            return None
        return {"id": minecraft_uuid.replace("-", ""), "name": "FuzzAlice"}

    @app.get("/discord/authorize")
    async def discord_authorize(request: Request) -> dict[str, str]:
        state.observe(request)
        return {"status": "synthetic"}

    @app.post("/discord/api/oauth2/token")
    async def discord_token(
        request: Request,
        response: Response,
    ) -> dict[str, str | int]:
        state.observe(request)
        body = await request.body()
        try:
            values = parse_qs(body.decode()) if len(body) <= MAX_TOKEN_REQUEST_BYTES else {}
            code = values.get("code", [None])[0]
        except UnicodeDecodeError:
            code = None
        if code != OAUTH_CODE_ALICE:
            response.status_code = 400
            return {"error": "invalid_grant"}
        return {"access_token": OAUTH_TOKEN_ALICE, "token_type": "Bearer", "expires_in": 600}

    @app.get("/discord/api/users/@me")
    async def discord_identity(request: Request, response: Response) -> dict[str, str]:
        state.observe(request)
        if request.headers.get("authorization") != f"Bearer {OAUTH_TOKEN_ALICE}":
            response.status_code = 401
            return {"message": "Unauthorized"}
        return {"id": str(DISCORD_ALICE), "username": "fuzz-alice"}

    @app.get("/discord/api/guilds/{guild_id}/members/{discord_id}")
    async def discord_member(
        guild_id: int,
        discord_id: int,
        request: Request,
        response: Response,
    ) -> dict[str, list[str] | str]:
        state.observe(request)
        if guild_id != DISCORD_GUILD or discord_id != DISCORD_ALICE:
            response.status_code = 404
            return {"message": "Unknown Member"}
        return {"roles": [str(DISCORD_TRUSTED_ROLE)]}

    return app


def main() -> None:
    """Run the fake service only on API-container loopback."""
    nonce = os.environ.get(CONTROL_NONCE_ENV, "")
    try:
        port = int(os.environ.get(FAKE_PORT_ENV, "8101"))
    except ValueError:
        raise SystemExit(f"{FAKE_PORT_ENV} must be an integer") from None
    if not 1 <= port <= 65535:
        raise SystemExit(f"{FAKE_PORT_ENV} must be between 1 and 65535")
    app = create_fake_upstream_app(nonce)
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
