"""Authentication persistence ports."""

from typing import Protocol

from whenever import Instant

from squid.accounts.domain import AccountConsent, IdentityProvider
from squid.accounts.models import Account
from squid.auth.domain.sessions import WebSessionIdentity
from squid.auth.models.entities import ApiKey
from squid.auth.models.sessions import OAuthState
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import Pattern
from squid.unit_of_work import UnitOfWork


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


class ApiKeyUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def api_keys(self) -> ApiKeyRepository: ...


class BrowserAccountRepository(Protocol):
    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account: ...


class WebSessionUnitOfWork(UnitOfWork, Protocol):
    @property
    def accounts(self) -> BrowserAccountRepository: ...

    @property
    def web_sessions(self) -> WebSessionRepository: ...
