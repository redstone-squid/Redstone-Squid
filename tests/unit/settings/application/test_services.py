"""Settings application service tests."""

from collections.abc import Iterable
from typing import Unpack, cast

from squid.settings.application import SettingsService
from squid.settings.domain import Setting, SettingOptions


class FakeSettingsRepository:
    def __init__(self) -> None:
        self.values = SettingOptions()

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:
        return {}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        if setting == "Staff":
            return self.values.get("Staff", [])
        if setting == "Trusted":
            return self.values.get("Trusted", [])
        return cast(int | list[int] | None, self.values.get(setting))

    async def get_all(self, server_id: int) -> SettingOptions:
        return self.values

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        self.values.update(settings)

    async def on_guild_join(self, server_id: int) -> None:
        return None

    async def on_guild_remove(self, server_id: int) -> None:
        return None


async def test_settings_clear_uses_shape_specific_empty_values() -> None:
    repository = FakeSettingsRepository()
    service = SettingsService(repository)

    await service.clear(1, "Vote")
    await service.clear(1, "Staff")

    assert repository.values == {"Vote": None, "Staff": []}
