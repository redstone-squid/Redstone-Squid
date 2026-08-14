"""Server settings application services."""

from collections.abc import Iterable, Mapping

from squid.settings.application.ports import SettingsStore
from squid.settings.domain import ScalarChannelSetting, Setting, SettingOptions


class SettingsService:
    """Validate and apply server-setting changes."""

    def __init__(self, repository: SettingsStore):
        self._repository = repository

    async def guild_joined(self, server_id: int) -> None:
        await self._repository.on_guild_join(server_id)

    async def guild_removed(self, server_id: int) -> None:
        await self._repository.on_guild_remove(server_id)

    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | None]:
        # Every remaining setting is a channel id, so the overloads that used to
        # distinguish role lists from channels have nothing left to distinguish.
        return await self._repository.get(server_ids, setting)

    async def get(self, server_id: int, setting: Setting) -> int | None:
        return await self._repository.get_single(server_id, setting)

    async def get_all(self, server_id: int) -> SettingOptions:
        return await self._repository.get_all(server_id)

    async def set_channel(self, server_id: int, setting: ScalarChannelSetting, channel_id: int) -> None:
        await self._set(server_id, setting, channel_id)

    async def clear(self, server_id: int, setting: Setting) -> None:
        await self._set(server_id, setting, None)

    async def get_locale(self, server_id: int) -> str | None:
        """Get the server's admin-configured locale override, if any."""
        return await self._repository.get_locale(server_id)

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        """Set or clear the server's admin-configured locale override."""
        await self._repository.set_locale(server_id, locale)

    async def _set(self, server_id: int, setting: Setting, value: int | None) -> None:
        settings = SettingOptions()
        settings[setting] = value  # type: ignore[literal-required, typeddict-item]
        await self._repository.set(server_id, **settings)
