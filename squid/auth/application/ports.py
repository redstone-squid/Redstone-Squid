"""Authentication persistence ports."""

from typing import Protocol

from whenever import Instant

from squid.auth.domain import ApiKey
from squid.auth.domain.sessions import OAuthState, WebSessionIdentity
from squid.permissions.domain import Pattern


class WebSessionRepository(Protocol):
    """Persistence required for OAuth state and opaque browser sessions."""

    async def save_state(self, state: OAuthState) -> None:
        """Store one-time PKCE state for a later callback to redeem."""
        ...

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None:
        """Delete and return *state*, or ``None`` if unknown or already expired at *now*.

        The delete happens whether or not the state has expired, so no state is redeemable twice.
        """
        ...

    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str:
        """Store a session for the hashed token and return its opaque session ID."""
        ...

    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None:
        """Return the identity behind an unrevoked, unexpired session, recording *now* as last seen."""
        ...

    async def revoke(self, token_hash: bytes, *, now: Instant) -> None:
        """Mark the session for *token_hash* revoked at *now*; unknown hashes are a no-op."""
        ...


class ApiKeyRepository(Protocol):
    """Persistence operations required by :class:`ApiKeyService`."""

    async def add(
        self,
        *,
        key_id: str,
        secret_hash: bytes,
        label: str,
        scopes: frozenset[Pattern],
        owner_account_id: int | None,
        created_by_account_id: int | None,
        expires_at: Instant | None,
    ) -> ApiKey:
        """Insert a credential and return it as stored."""
        ...

    async def get_by_key_id(self, key_id: str) -> ApiKey | None:
        """Return the credential with this public key ID regardless of revocation or expiry."""
        ...

    async def touch_last_used(
        self,
        key_id: str,
        *,
        used_at: Instant,
        used_ip: str | None,
        older_than: Instant,
    ) -> None:
        """Record use only when the prior timestamp predates *older_than*."""
        ...
