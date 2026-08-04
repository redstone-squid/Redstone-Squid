"""Server settings application services."""

from collections.abc import Iterable, Mapping
from typing import overload

from squid.settings.application.ports import SettingsStore
from squid.settings.domain import ListRoleSetting, ScalarChannelSetting, Setting, SettingOptions


class SettingsService:
    """Validate and apply server-setting changes."""

    def __init__(self, repository: SettingsStore):
        self._repository = repository

    async def guild_joined(self, server_id: int) -> None:
        await self._repository.on_guild_join(server_id)

    async def guild_removed(self, server_id: int) -> None:
        await self._repository.on_guild_remove(server_id)

    @overload
    async def get_many(self, server_ids: Iterable[int], setting: ScalarChannelSetting) -> Mapping[int, int | None]: ...

    @overload
    async def get_many(self, server_ids: Iterable[int], setting: ListRoleSetting) -> Mapping[int, list[int]]: ...

    @overload
    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | list[int] | None]: ...

    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | list[int] | None]:
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
        value: int | list[int] | None = [] if setting == "Trusted" else None
        await self._set(server_id, setting, value)

    async def get_locale(self, server_id: int) -> str | None:
        """Get the server's admin-configured locale override, if any."""
        return await self._repository.get_locale(server_id)

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        """Set or clear the server's admin-configured locale override."""
        await self._repository.set_locale(server_id, locale)

    async def _set(self, server_id: int, setting: Setting, value: int | list[int] | None) -> None:
        settings = SettingOptions()
        settings[setting] = value  # type: ignore[literal-required, typeddict-item]
        await self._repository.set(server_id, **settings)
