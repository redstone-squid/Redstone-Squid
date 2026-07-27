"""Framework-independent server settings application service."""

from collections.abc import Iterable
from typing import Protocol, Unpack, overload

from squid.db.schema import ListRoleSetting, ScalarChannelSetting, Setting
from squid.db.server_settings import SettingOptions


class SettingsStore(Protocol):
    """Persistence operations required by :class:`SettingsService`."""

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]: ...

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None: ...

    async def get_all(self, server_id: int) -> SettingOptions: ...

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None: ...

    async def on_guild_join(self, server_id: int) -> None: ...

    async def on_guild_remove(self, server_id: int) -> None: ...


class SettingsService:
    """Validate and apply server-setting changes."""

    def __init__(self, repository: SettingsStore):
        self._repository = repository

    async def guild_joined(self, server_id: int) -> None:
        await self._repository.on_guild_join(server_id)

    async def guild_removed(self, server_id: int) -> None:
        await self._repository.on_guild_remove(server_id)

    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:
        return await self._repository.get(server_ids, setting)

    @overload
    async def get(self, server_id: int, setting: ScalarChannelSetting) -> int | None: ...

    @overload
    async def get(self, server_id: int, setting: ListRoleSetting) -> list[int]: ...

    @overload
    async def get(self, server_id: int, setting: Setting) -> int | list[int] | None: ...

    async def get(self, server_id: int, setting: Setting) -> int | list[int] | None:
        return await self._repository.get_single(server_id, setting)

    async def get_all(self, server_id: int) -> SettingOptions:
        return await self._repository.get_all(server_id)

    async def set_channel(self, server_id: int, setting: ScalarChannelSetting, channel_id: int) -> None:
        await self._set(server_id, setting, channel_id)

    async def set_roles(self, server_id: int, setting: ListRoleSetting, role_ids: list[int]) -> None:
        await self._set(server_id, setting, role_ids)

    async def clear(self, server_id: int, setting: Setting) -> None:
        value: int | list[int] | None = [] if setting in ("Staff", "Trusted") else None
        await self._set(server_id, setting, value)

    async def _set(self, server_id: int, setting: Setting, value: int | list[int] | None) -> None:
        settings = SettingOptions()
        settings[setting] = value  # type: ignore[literal-required, typeddict-item]
        await self._repository.set(server_id, **settings)
