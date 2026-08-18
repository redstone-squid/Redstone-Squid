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

    Exported so test fixtures seeding `web_sessions` reuse the construction
    instead of re-deriving it. `docs/credential-hashing.md` records why a keyed
    SHA-256 is right for a `token_urlsafe(32)` secret and why a password KDF is
    not.
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
        """Persist one-time PKCE state and return the provider's authorize URL."""
        provider = self._provider(slug)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        await self._repository.save_state(
            OAuthState(state, verifier, redirect_to, self._now().add(minutes=10), provider=provider.provider)
        )
        return provider.authorize_url(state=state, code_challenge=challenge)

    async def callback(self, slug: str, code: str, state: str, *, user_agent: str | None) -> tuple[str, str | None]:
        """Consume state, exchange the code, and issue an opaque session token."""
        provider = self._provider(slug)
        saved = await self._repository.consume_state(state, now=self._now())
        if saved is None:
            msg = "OAuth state is invalid or expired."
            raise AuthenticationError(msg)
        if saved.provider is not provider.provider:
            # A state minted for provider A must not be redeemable at provider B's
            # callback: that is the IdP mix-up class, and the state is already spent by
            # the time we get here, so the only thing left to do is refuse.
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
        """Resolve a URL segment, or say the login does not exist here.

        A 404 rather than a credential failure: "this deployment has no GitHub login" is
        a fact about the resource, not about who is asking.
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
