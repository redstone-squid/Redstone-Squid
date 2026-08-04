"""Settings application service tests."""

from collections.abc import Iterable
from typing import Unpack, cast

from squid.settings.application import SettingsService
from squid.settings.domain import Setting, SettingOptions


class FakeSettingsRepository:
    def __init__(self) -> None:
        self.values = SettingOptions()
        self.locale: str | None = None

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:
        return {}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        if setting == "Trusted":
            return self.values.get("Trusted", [])
        return cast(int | list[int] | None, self.values.get(setting))

    async def get_all(self, server_id: int) -> SettingOptions:
        return self.values

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        self.values.update(settings)

    async def get_locale(self, server_id: int) -> str | None:
        return self.locale

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        self.locale = locale

    async def on_guild_join(self, server_id: int) -> None:
        return None

    async def on_guild_remove(self, server_id: int) -> None:
        return None


async def test_settings_clear_uses_shape_specific_empty_values() -> None:
    repository = FakeSettingsRepository()
    service = SettingsService(repository)

    await service.clear(1, "Vote")
    await service.clear(1, "Trusted")

    assert repository.values == {"Vote": None, "Trusted": []}


async def test_settings_locale_round_trips() -> None:
    repository = FakeSettingsRepository()
    service = SettingsService(repository)

    assert await service.get_locale(1) is None
    await service.set_locale(1, "zh-CN")
    assert await service.get_locale(1) == "zh-CN"
