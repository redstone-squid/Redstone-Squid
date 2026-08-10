"""Application ports for bot authorization."""

from collections.abc import Sequence
from typing import Protocol

from squid.permissions.domain import GlobalAdministrator


class GlobalAdministratorStore(Protocol):
    """Persistence operations required by :class:`AuthorizationService`."""

    async def contains(self, account_id: int) -> bool: ...

    async def list(self) -> Sequence[GlobalAdministrator]: ...

    async def grant(self, account_id: int, granted_by_account_id: int) -> GlobalAdministrator: ...

    async def revoke(self, account_id: int) -> bool: ...
