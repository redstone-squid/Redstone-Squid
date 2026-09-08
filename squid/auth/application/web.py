"""Browser-session orchestration over any authorization-code identity provider."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping

from whenever import Instant

from squid.accounts.application import AccountService
from squid.auth.application.ports import WebSessionRepository
from squid.auth.application.providers import OAuthProvider
from squid.auth.domain.sessions import OAuthState
from squid.core.errors import AuthenticationError, NotFoundError, ValidationError


def hash_web_session_token(pepper: bytes, token: str) -> bytes:
    """Return the digest stored for an opaque web-session token.

    A keyed SHA-256, not a password KDF: the token is a `token_urlsafe(32)` secret with no
    low-entropy input space for a work factor to protect. See `docs/credential-hashing.md`.
    """
    # codeql[py/weak-sensitive-data-hashing]
    return hmac.digest(pepper, token.encode(), hashlib.sha256)  # 256-bit random session token


class WebSessionService:
    """Exchange one external identity, then issue a revocable local session."""

    def __init__(
        self,
        repository: WebSessionRepository,
        accounts: AccountService,
        providers: Mapping[str, OAuthProvider],
        session_ttl_hours: int,
        pepper: str,
        *,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        self._repository = repository
        self._accounts = accounts
        self._providers = providers
        self._session_ttl_hours = session_ttl_hours
        self._pepper = pepper.encode()
        self._now = now

    @property
    def configured(self) -> bool:
        """Whether this deployment can log anybody in at all."""
        return bool(self._providers)

    async def authorize_url(self, slug: str, redirect_to: str | None) -> str:
        """Persist one-time PKCE state, good for ten minutes, and return the provider's authorize URL.

        Raises `ValidationError` when no provider is configured and `NotFoundError` for an unknown
        *slug*.
        """
        provider = self._provider(slug)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        await self._repository.save_state(
            OAuthState(state, verifier, redirect_to, self._now().add(minutes=10), provider=provider.provider)
        )
        return provider.authorize_url(state=state, code_challenge=challenge)

    async def callback(self, slug: str, code: str, state: str, *, user_agent: str | None) -> tuple[str, str | None]:
        """Consume state, exchange the code, and return an opaque session token and its redirect.

        The token is returned once and only its digest is stored; the session expires after the
        configured TTL or when `logout` revokes it.

        Raises:
            AuthenticationError: If *state* is unknown, expired, already spent, or was minted for
                another provider.
            ServiceUnavailableError: If the provider's token or profile exchange fails.
            ValidationError: If no provider is configured.
            NotFoundError: If *slug* names no configured provider.
        """
        provider = self._provider(slug)
        saved = await self._repository.consume_state(state, now=self._now())
        if saved is None:
            msg = "OAuth state is invalid or expired."
            raise AuthenticationError(msg)
        if saved.provider is not provider.provider:
            # IdP mix-up: a state minted for provider A must not be redeemable at provider B's
            # callback. The state is already spent by now, so refusing is all that is left.
            msg = "OAuth state was issued for a different provider."
            raise AuthenticationError(msg)
        identity = await provider.fetch_identity(code=code, code_verifier=saved.code_verifier)
        account = await self._accounts.get_or_create_identity(identity.provider, identity.subject)
        assert account.id is not None
        token = secrets.token_urlsafe(32)
        await self._repository.create_session(
            token_hash=self.hash_token(token),
            account_id=account.id,
            expires_at=self._now().add(hours=self._session_ttl_hours),
            user_agent=user_agent,
        )
        return token, saved.redirect_to

    async def authenticate(self, token: str):
        return await self._repository.authenticate(self.hash_token(token), now=self._now())

    async def logout(self, token: str) -> None:
        await self._repository.revoke(self.hash_token(token), now=self._now())

    def hash_token(self, token: str) -> bytes:
        return hash_web_session_token(self._pepper, token)

    def _provider(self, slug: str) -> OAuthProvider:
        """Resolve a URL segment to a provider.

        Raises `ValidationError` when nothing is configured, and `NotFoundError` for an unknown
        slug -- "this deployment has no GitHub login" is a fact about the resource, not about who
        is asking, so it is a 404 rather than a credential failure.
        """
        if not self._providers:
            msg = "Browser login is not configured."
            raise ValidationError(msg)
        provider = self._providers.get(slug)
        if provider is None:
            raise NotFoundError(
                context={"provider": slug},
                public_context={"provider": slug},
                resource="identity_provider",
            )
        return provider
