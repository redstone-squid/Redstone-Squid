"""Discord OAuth2 and opaque web-session orchestration."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlencode

import httpx
from whenever import Instant

from squid.auth.domain.sessions import OAuthState, WebSessionIdentity
from squid.config import OAuthConfig
from squid.core.errors import AuthenticationError, ServiceUnavailableError, ValidationError
from squid.users.application import UserService
from squid.users.domain import CONSENT_CUTOFF


class WebSessionRepository(Protocol):
    """Persistence required for OAuth state and opaque sessions."""

    async def save_state(self, state: OAuthState) -> None: ...

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None: ...

    async def create_session(
        self, *, token_hash: bytes, user_id: int, expires_at: Instant, user_agent: str | None
    ) -> str: ...

    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None: ...

    async def revoke(self, token_hash: bytes, *, now: Instant) -> None: ...


class DiscordOAuthService:
    """Exchange Discord identity once, then issue a revocable local session."""

    def __init__(
        self,
        repository: WebSessionRepository,
        users: UserService,
        config: OAuthConfig,
        pepper: str,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        self._repository = repository
        self._users = users
        self._config = config
        self._pepper = pepper.encode()
        self._client = client or httpx.AsyncClient(timeout=10)
        self._owns_client = client is None
        self._now = now

    @property
    def configured(self) -> bool:
        return self._config.discord_client_id is not None

    async def authorize_url(self, redirect_to: str | None) -> str:
        """Persist one-time PKCE state and return Discord's authorize URL."""
        self._require_configured()
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        await self._repository.save_state(OAuthState(state, verifier, redirect_to, self._now().add(minutes=10)))
        query = urlencode(
            {
                "client_id": self._config.discord_client_id,
                "redirect_uri": str(self._config.redirect_uri),
                "response_type": "code",
                "scope": "identify",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"https://discord.com/oauth2/authorize?{query}"

    async def callback(self, code: str, state: str, *, user_agent: str | None) -> tuple[str, str | None]:
        """Consume state, exchange the code, and issue an opaque session token."""
        self._require_configured()
        saved = await self._repository.consume_state(state, now=self._now())
        if saved is None:
            msg = "OAuth state is invalid or expired."
            raise AuthenticationError(msg)
        try:
            client_secret = self._config.discord_client_secret
            if client_secret is None:
                raise self._discord_unavailable()
            token_response = await self._client.post(
                "https://discord.com/api/v10/oauth2/token",
                data={
                    "client_id": self._config.discord_client_id,
                    "client_secret": client_secret.get_secret_value(),
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": str(self._config.redirect_uri),
                    "code_verifier": saved.code_verifier,
                },
            )
            if token_response.status_code != 200:
                raise self._discord_unavailable()
            access_token = token_response.json()["access_token"]
            identity_response = await self._client.get(
                "https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bearer {access_token}"}
            )
            if identity_response.status_code != 200:
                raise self._discord_unavailable()
            discord_id = int(identity_response.json()["id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            msg = "Discord OAuth exchange failed."
            raise ServiceUnavailableError(msg, resource="discord") from error
        account = await self._users.get_or_create_account(discord_id)
        assert account.id is not None
        token = secrets.token_urlsafe(32)
        await self._repository.create_session(
            token_hash=self.hash_token(token),
            user_id=account.id,
            expires_at=self._now().add(hours=self._config.session_ttl_hours),
            user_agent=user_agent,
        )
        return token, saved.redirect_to

    async def authenticate(self, token: str) -> WebSessionIdentity | None:
        return await self._repository.authenticate(self.hash_token(token), now=self._now())

    async def logout(self, token: str) -> None:
        await self._repository.revoke(self.hash_token(token), now=self._now())

    def hash_token(self, token: str) -> bytes:
        return hmac.digest(self._pepper, token.encode(), hashlib.sha256)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _require_configured(self) -> None:
        if not self.configured:
            msg = "Discord OAuth is not configured."
            raise ValidationError(msg)

    @staticmethod
    def _discord_unavailable() -> ServiceUnavailableError:
        msg = "Discord OAuth exchange failed."
        return ServiceUnavailableError(msg, resource="discord")


def consent_pending(created_at: Instant | None, consent_version: str | None, current_version: str) -> bool:
    """Apply the explicit grandfather cutoff to the write-consent gate."""
    cutoff = Instant.parse_iso(CONSENT_CUTOFF)
    return created_at is not None and created_at >= cutoff and consent_version != current_version
