"""Authentication persistence ports."""

from typing import Protocol

from whenever import Instant

from squid.auth.domain import ApiKey
from squid.permissions.domain import Pattern


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
