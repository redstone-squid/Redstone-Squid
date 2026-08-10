"""Player-grant authentication at the shared API security boundary."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request

from squid.api.security import current_principal
from squid.core.errors import AuthenticationError
from squid.minecraft_auth.domain import (
    AuthenticatedPaperInstallation,
    MinecraftClientOrigin,
    MinecraftPlayerContext,
)

GRANT_ID = UUID("139533d8-3172-4b0f-bb86-c76603cd75af")
INSTALLATION_ID = UUID("a2b0b451-1591-42e0-ad75-165b43409eaf")
JAVA_UUID = UUID("d8de679a-3de4-4cb9-9f11-c961c72a3531")
PLAYER_TOKEN = f"sqpt_{GRANT_ID.hex}_{'p' * 43}"
INSTALLATION_SECRET = "s" * 43


class FakeInstallations:
    def __init__(self) -> None:
        self.token: str | None = None

    async def authenticate(self, token: str) -> AuthenticatedPaperInstallation:
        self.token = token
        return AuthenticatedPaperInstallation(INSTALLATION_ID, 9, 3)


class FakePlayers:
    def __init__(self) -> None:
        self.fabric_token: str | None = None
        self.paper_call: tuple[str, AuthenticatedPaperInstallation] | None = None

    async def authenticate_fabric_player(self, token: str) -> MinecraftPlayerContext:
        self.fabric_token = token
        return MinecraftPlayerContext(
            grant_id=GRANT_ID,
            account_id=42,
            java_uuid=JAVA_UUID,
            origin=MinecraftClientOrigin.FABRIC,
        )

    async def authenticate_paper_player(
        self,
        token: str,
        installation: AuthenticatedPaperInstallation,
    ) -> MinecraftPlayerContext:
        self.paper_call = (token, installation)
        return MinecraftPlayerContext(
            grant_id=GRANT_ID,
            account_id=42,
            java_uuid=JAVA_UUID,
            origin=MinecraftClientOrigin.PAPER,
            installation_id=installation.id,
        )


def request_with_services(
    players: FakePlayers,
    installations: FakeInstallations,
    *,
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    app = FastAPI()
    app.state.config = SimpleNamespace(api=SimpleNamespace(secret=SecretStr("bootstrap-secret")))
    app.state.runtime = SimpleNamespace(
        services=SimpleNamespace(
            minecraft_player_authorization=players,
            minecraft_installations=installations,
            api_keys=None,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/submissions/drafts/test",
            "headers": list(headers),
            "app": app,
        }
    )


async def test_fabric_player_token_derives_bound_principal_without_installation_headers() -> None:
    players = FakePlayers()
    request = request_with_services(players, FakeInstallations())

    principal = await current_principal(request, f"Bearer {PLAYER_TOKEN}")

    assert principal.kind == "minecraft_player"
    assert principal.account_id == 42
    assert principal.minecraft_origin == "fabric"
    assert principal.java_uuid == JAVA_UUID
    assert principal.installation_id is None
    assert players.fabric_token == PLAYER_TOKEN


async def test_paper_player_token_requires_and_authenticates_both_installation_headers() -> None:
    players = FakePlayers()
    installations = FakeInstallations()
    request = request_with_services(
        players,
        installations,
        headers=(
            (b"x-squid-installation-id", str(INSTALLATION_ID).encode()),
            (b"x-squid-installation-secret", INSTALLATION_SECRET.encode()),
        ),
    )

    principal = await current_principal(request, f"Bearer {PLAYER_TOKEN}")

    assert principal.minecraft_origin == "paper"
    assert principal.installation_id == INSTALLATION_ID
    assert installations.token == f"sqpi_{INSTALLATION_ID.hex}_{INSTALLATION_SECRET}"
    assert players.paper_call is not None


@pytest.mark.parametrize(
    "headers",
    [
        ((b"x-squid-installation-id", str(INSTALLATION_ID).encode()),),
        (
            (b"x-squid-installation-id", str(INSTALLATION_ID).encode()),
            (b"x-squid-installation-secret", b"s" * 513),
        ),
    ],
)
async def test_incomplete_or_oversized_paper_binding_fails_before_authentication(
    headers: tuple[tuple[bytes, bytes], ...],
) -> None:
    players = FakePlayers()
    installations = FakeInstallations()

    with pytest.raises(AuthenticationError):
        await current_principal(request_with_services(players, installations, headers=headers), f"Bearer {PLAYER_TOKEN}")

    assert players.fabric_token is None
    assert players.paper_call is None
    assert installations.token is None
