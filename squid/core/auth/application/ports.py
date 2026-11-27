"""Authentication persistence ports."""

from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.accounts.domain import IdentityProvider, PrivacyNoticeReceipt
from squid.core.accounts.models import Account
from squid.core.auth.domain.sessions import WebSessionIdentity
from squid.core.auth.models.entities import ApiKey
from squid.core.auth.models.sessions import ImpersonationSession, OAuthState
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.domain import Pattern
from squid.core.unit_of_work import UnitOfWork


class WebSessionRepository(Protocol):
    """Store pending OAuth logins and the local browser sessions they create.

    Consuming state removes one login attempt so its callback cannot be reused.
    Session authentication returns the linked account.
    WebSessionService commits these operations through its unit of work.
    """

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
    async def link_browser_identity(self, account_id: int, provider: IdentityProvider, subject: str) -> bool: ...

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: PrivacyNoticeReceipt | None = None
    ) -> Account: ...


class WebSessionUnitOfWork(UnitOfWork, Protocol):
    @property
    def accounts(self) -> BrowserAccountRepository: ...

    @property
    def web_sessions(self) -> WebSessionRepository: ...


class ImpersonationRepository(Protocol):
    async def add(self, session: ImpersonationSession) -> None: ...

    async def locked(self, session_id: UUID, operator_id: int, binding: str) -> ImpersonationSession | None: ...

    async def record_use(self, session_id: UUID, operation: str) -> UUID: ...


class ImpersonationAccounts(Protocol):
    async def get_by_id(self, account_id: int) -> Account | None: ...


class ImpersonationUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def impersonation(self) -> ImpersonationRepository: ...

    @property
    def accounts(self) -> ImpersonationAccounts: ...
