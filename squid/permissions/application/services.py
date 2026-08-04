"""Bot authorization application services."""

from collections.abc import Sequence

from squid.permissions.application.ports import GlobalAdministratorStore
from squid.permissions.domain import GlobalAdministrator


class AuthorizationService:
    """Manage bot-wide administrator grants."""

    def __init__(self, store: GlobalAdministratorStore):
        self._store = store

    async def is_global_administrator(self, discord_id: int) -> bool:
        """Return whether a Discord user has an active global administrator grant."""
        return await self._store.contains(discord_id)

    async def list_global_administrators(self) -> Sequence[GlobalAdministrator]:
        """List global administrators in grant order."""
        return await self._store.list()

    async def grant_global_administrator(self, discord_id: int, *, granted_by_discord_id: int) -> GlobalAdministrator:
        """Grant global administrator access, returning the existing grant when present."""
        return await self._store.grant(discord_id, granted_by_discord_id)

    async def revoke_global_administrator(self, discord_id: int) -> bool:
        """Revoke global administrator access if an active grant exists."""
        return await self._store.revoke(discord_id)
