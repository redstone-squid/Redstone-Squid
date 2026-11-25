"""Turn an external provider login into a local browser session.

Starting a login stores OAuth state: a one-time identifier linking the callback
to its chosen provider, a PKCE verifier used to redeem the authorization code,
and the requested redirect. The provider receives the verifier's hash as its
challenge; the verifier stays here until the callback.

The callback exchanges the code for a verified provider identity and finds or
creates its Squid account. It then issues a random browser-session token whose
digest is stored locally. The API route puts the token in the browser cookie;
subsequent requests authenticate that local session without contacting the
provider again. Revoking it therefore does not require a provider logout.
"""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping

from whenever import Instant

from squid.core.actors import ActingContext, UserActor, application_operation
from squid.core.auth.application.ports import WebSessionUnitOfWork
from squid.core.auth.application.providers import OAuthProvider
from squid.core.auth.models.sessions import OAuthState
from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.unit_of_work import UnitOfWorkFactory


def hash_web_session_token(pepper: bytes, token: str) -> bytes:
    """Return the HMAC-SHA256 digest stored for a browser-session token.

    Tokens contain 32 random bytes. The deployment pepper is kept outside the
    database; see `docs/credential-hashing.md` for the hashing rationale.
    """
    # codeql[py/weak-sensitive-data-hashing]
    return hmac.digest(pepper, token.encode(), hashlib.sha256)  # 256-bit random session token


class CallbackFailure(AuthenticationError):
    """A public callback error with an optional validated retry destination."""

    def __init__(self, code: str, redirect_to: str | None = None):
        super().__init__(code)
        self.callback_code = code
        self.redirect_to = redirect_to


class WebSessionService:
    """Exchange one external identity, then issue a revocable local session."""

    def __init__(
        self,
        providers: Mapping[str, OAuthProvider],
        session_ttl_hours: int,
        pepper: str,
        *,
        uow_factory: UnitOfWorkFactory[WebSessionUnitOfWork],
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        self._uow_factory = uow_factory
        self._providers = providers
        self._session_ttl_hours = session_ttl_hours
        self._pepper = pepper.encode()
        self._now = now

    @property
    def configured(self) -> bool:
        """Return whether at least one login provider is configured."""
        return bool(self._providers)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    @application_operation
    async def authorize_url(
        self, slug: str, redirect_to: str | None, *, context: ActingContext, link: bool = False
    ) -> str:
        """Store a ten-minute login attempt and return the provider's authorization URL.

        `redirect_to` is retained for the callback. The API route must validate it
        before calling this method; the service does not restrict redirect targets.
        """
        async with self._uow_factory() as uow:
            provider = self._provider(slug)
            state = secrets.token_urlsafe(24)
            if link:
                account_id = self._browser_account(context)
                if account_id is None:
                    raise AuthorizationError
                binding = self._link_binding(account_id, state)
                state = f"link.{state}.{binding}"
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            await uow.web_sessions.save_state(
                OAuthState(
                    provider=provider.provider,
                    state=state,
                    code_verifier=verifier,
                    redirect_to=redirect_to,
                    expires_at=self._now().add(minutes=10),
                )
            )
            result = provider.authorize_url(state=state, code_challenge=challenge)
            await uow.commit()
            return result

    @application_operation
    async def callback(
        self,
        slug: str,
        code: str | None,
        state: str,
        *,
        user_agent: str | None,
        error: str | None = None,
        context: ActingContext,
        consent_current: bool = False,
    ) -> tuple[str, str | None]:
        """Consume OAuth state, exchange the code, and return a session token and redirect.

        State consumption commits before contacting the provider. A provider mismatch
        or failed exchange therefore spends the state, and the browser must start a
        new login. Account creation and session issuance share a second transaction.
        """
        provider = self._provider(slug)
        async with self._uow_factory() as uow:
            saved = await uow.web_sessions.consume_state(state, now=self._now())
            await uow.commit()
        if saved is None or saved.provider is not provider.provider:
            msg = "invalid_state"
            raise CallbackFailure(msg)
        current_account_id = self._browser_account(context) if consent_current else None
        linking = saved.state.startswith("link.")
        if linking:
            _, nonce, binding = saved.state.split(".")
            if current_account_id is None or not hmac.compare_digest(
                binding, self._link_binding(current_account_id, nonce)
            ):
                msg = "invalid_state"
                raise CallbackFailure(msg)
        if error or not code:
            raise CallbackFailure(
                "cancelled" if error == "access_denied" else "provider_unavailable", saved.redirect_to
            )
        try:
            identity = await provider.fetch_identity(code=code, code_verifier=saved.code_verifier)
        except ServiceUnavailableError as exc:
            msg = "provider_unavailable"
            raise CallbackFailure(msg, saved.redirect_to) from exc
        async with self._uow_factory() as uow:
            if linking and current_account_id is not None:
                if not await uow.accounts.link_browser_identity(
                    current_account_id, identity.provider, identity.subject
                ):
                    msg = "identity_in_use"
                    raise CallbackFailure(msg, saved.redirect_to)
                account_id = current_account_id
            else:
                account = await uow.accounts.get_or_create_identity(identity.provider, identity.subject)
                account_id = account.id
            token = secrets.token_urlsafe(32)
            await uow.web_sessions.create_session(
                token_hash=self.hash_token(token),
                account_id=account_id,
                expires_at=self._now().add(hours=self._session_ttl_hours),
                user_agent=user_agent,
            )
            await uow.commit()
            return token, saved.redirect_to

    async def authenticate(self, token: str):
        """Return an active session identity and record its use, or None if invalid."""
        async with self._uow_factory() as uow:
            result = await uow.web_sessions.authenticate(self.hash_token(token), now=self._now())
            await uow.commit()
            return result

    @application_operation
    async def logout(self, token: str, *, context: ActingContext) -> None:
        """Revoke the matching session, doing nothing if the token is unknown."""
        async with self._uow_factory() as uow:
            await uow.web_sessions.revoke(self.hash_token(token), now=self._now())
            await uow.commit()

    def hash_token(self, token: str) -> bytes:
        return hash_web_session_token(self._pepper, token)

    @staticmethod
    def _browser_account(context: ActingContext) -> int | None:
        actor = context.effective
        if (
            not isinstance(actor, UserActor)
            or context.session_id is not None
            or not (actor.credential_id or "").startswith("browser:")
        ):
            return None
        return actor.account_id

    def _link_binding(self, account_id: int, nonce: str) -> str:
        return hmac.digest(self._pepper, f"link:{account_id}:{nonce}".encode(), hashlib.sha256).hex()

    def _provider(self, slug: str) -> OAuthProvider:
        """Return a configured provider by URL slug.

        Raise ValidationError when browser login is disabled, or NotFoundError when
        login is enabled but the requested provider is absent.
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
