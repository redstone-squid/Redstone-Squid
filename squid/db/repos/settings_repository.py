"""Adapter exposing server settings to the application layer."""

from collections.abc import Iterable
from typing import Unpack

from squid.db.schema import Setting
from squid.db.server_settings import ServerSettingManager, SettingOptions


class SettingsRepository:
    """Normalize the manager's overloaded methods for application services."""

    def __init__(self, manager: ServerSettingManager):
        self._manager = manager

    async def get(
        self,
        server_ids: Iterable[int],
        setting: Setting,
    ) -> dict[int, int | list[int] | None]:
        values = await self._manager.get(server_ids, setting)
        return {server_id: value for server_id, value in values.items()}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        return await self._manager.get_single(server_id, setting)

    async def get_all(self, server_id: int) -> SettingOptions:
        return await self._manager.get_all(server_id)

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        await self._manager.set(server_id, **settings)

    async def on_guild_join(self, server_id: int) -> None:
        await self._manager.on_guild_join(server_id)

    async def on_guild_remove(self, server_id: int) -> None:
        await self._manager.on_guild_remove(server_id)
