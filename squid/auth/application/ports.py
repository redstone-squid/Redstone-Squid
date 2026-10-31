"""Authentication persistence ports."""

from typing import Protocol

from whenever import Instant

from squid.auth.domain import ApiKey
from squid.auth.domain.sessions import OAuthState, WebSessionIdentity
from squid.permissions.domain import Pattern


class WebSessionRepository(Protocol):
    """Persistence required for OAuth state and opaque browser sessions."""

    async def save_state(self, state: OAuthState) -> None: ...

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None: ...

    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str: ...

    async def authenticate(self, token_hash: bytes, *, now: Instant) -> WebSessionIdentity | None: ...

    async def revoke(self, token_hash: bytes, *, now: Instant) -> None: ...


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
    ) -> ApiKey: ...

    async def get_by_key_id(self, key_id: str) -> ApiKey | None: ...

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
