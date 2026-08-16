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

from squid.accounts.application import AccountService
from squid.accounts.domain import CONSENT_CUTOFF, IdentityProvider
from squid.auth.domain.sessions import OAuthState, WebSessionIdentity
from squid.config import OAuthConfig, UpstreamHttpConfig
from squid.core.errors import AuthenticationError, ServiceUnavailableError, ValidationError


def hash_web_session_token(pepper: bytes, token: str) -> bytes:
    """Return the digest stored for an opaque web-session token.

    Exported so test fixtures seeding `web_sessions` reuse the construction
    instead of re-deriving it. `docs/credential-hashing.md` records why a keyed
    SHA-256 is right for a `token_urlsafe(32)` secret and why a password KDF is
    not.
    """
    # codeql[py/weak-sensitive-data-hashing]
    return hmac.digest(pepper, token.encode(), hashlib.sha256)  # 256-bit random session token


class WebSessionRepository(Protocol):
    """Persistence required for OAuth state and opaque sessions."""

    async def save_state(self, state: OAuthState) -> None: ...

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None: ...

    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        discord_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str: ...

    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None: ...

    async def revoke(self, token_hash: bytes, *, now: Instant) -> None: ...


class DiscordOAuthService:
    """Exchange Discord identity once, then issue a revocable local session."""

    def __init__(
        self,
        repository: WebSessionRepository,
        accounts: AccountService,
        config: OAuthConfig,
        pepper: str,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], Instant] = Instant.now,
        upstreams: UpstreamHttpConfig | None = None,
    ) -> None:
        self._repository = repository
        self._accounts = accounts
        self._config = config
        self._pepper = pepper.encode()
        self._client = client or httpx.AsyncClient(timeout=10)
        self._owns_client = client is None
        self._now = now
        resolved_upstreams = upstreams or UpstreamHttpConfig()
        self._discord_api_url = str(resolved_upstreams.discord_api_url).rstrip("/")
        self._discord_authorize_url = str(resolved_upstreams.discord_authorize_url).rstrip("/")

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
        return f"{self._discord_authorize_url}?{query}"

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
                f"{self._discord_api_url}/oauth2/token",
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
                f"{self._discord_api_url}/users/@me", headers={"Authorization": f"Bearer {access_token}"}
            )
            if identity_response.status_code != 200:
                raise self._discord_unavailable()
            discord_id = int(identity_response.json()["id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            msg = "Discord OAuth exchange failed."
            raise ServiceUnavailableError(msg, resource="discord") from error
        account = await self._accounts.get_or_create_identity(IdentityProvider.DISCORD, str(discord_id))
        assert account.id is not None
        token = secrets.token_urlsafe(32)
        await self._repository.create_session(
            token_hash=self.hash_token(token),
            account_id=account.id,
            discord_id=discord_id,
            expires_at=self._now().add(hours=self._config.session_ttl_hours),
            user_agent=user_agent,
        )
        return token, saved.redirect_to

    async def authenticate(self, token: str) -> WebSessionIdentity | None:
        return await self._repository.authenticate(self.hash_token(token), now=self._now())

    async def logout(self, token: str) -> None:
        await self._repository.revoke(self.hash_token(token), now=self._now())

    def hash_token(self, token: str) -> bytes:
        return hash_web_session_token(self._pepper, token)

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
