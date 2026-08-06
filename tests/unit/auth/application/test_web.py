"""Discord OAuth and opaque-session service tests."""

from unittest.mock import AsyncMock

import httpx
import pytest
from whenever import Instant

from squid.auth.application.web import DiscordOAuthService, consent_pending
from squid.auth.domain.sessions import OAuthState
from squid.config import OAuthConfig


class SessionRepository:
    """Small stateful repository fake for the OAuth exchange."""

    def __init__(self) -> None:
        self.state: OAuthState | None = None
        self.token_hash: bytes | None = None

    async def save_state(self, state: OAuthState) -> None:
        self.state = state

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None:
        if self.state is None or self.state.state != state or self.state.expires_at <= now:
            return None
        saved, self.state = self.state, None
        return saved

    async def create_session(
        self, *, token_hash: bytes, user_id: int, expires_at: Instant, user_agent: str | None
    ) -> str:
        del user_id, expires_at, user_agent
        self.token_hash = token_hash
        return "session-id"

    authenticate = AsyncMock(return_value=None)
    revoke = AsyncMock()


@pytest.mark.asyncio
async def test_oauth_state_is_durable_and_callback_issues_hashed_session() -> None:
    repository = SessionRepository()
    users = AsyncMock()
    users.get_or_create_account.return_value.id = 42
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"access_token": "discord-token"} if request.url.path.endswith("/oauth2/token") else {"id": "123"},
        )
    )
    service = DiscordOAuthService(
        repository,
        users,
        OAuthConfig(
            discord_client_id="client",
            discord_client_secret="secret",
            redirect_uri="https://api.example/v1/auth/discord/callback",
        ),
        "session-pepper-for-tests",
        client=httpx.AsyncClient(transport=transport),
        now=lambda: Instant.from_utc(2026, 8, 5),
    )

    authorize_url = await service.authorize_url("/account")
    assert repository.state is not None
    assert "code_challenge_method=S256" in authorize_url

    token, redirect_to = await service.callback("code", repository.state.state, user_agent="test")

    users.get_or_create_account.assert_awaited_once_with(123)
    assert redirect_to == "/account"
    assert repository.token_hash == service.hash_token(token)
    assert token.encode() not in repository.token_hash


def test_consent_gate_grandfathers_accounts_before_cutoff() -> None:
    assert not consent_pending(Instant.from_utc(2026, 8, 3), None, "current")
    assert consent_pending(Instant.from_utc(2026, 8, 5), None, "current")
    assert not consent_pending(Instant.from_utc(2026, 8, 5), "current", "current")
