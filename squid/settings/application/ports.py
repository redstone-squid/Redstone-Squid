"""Server settings application ports."""

from collections.abc import Iterable
from typing import Protocol, Unpack

from squid.settings.domain import Setting, SettingOptions


class SettingsStore(Protocol):
    """Persistence operations required by :class:`SettingsService`."""

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]: ...

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None: ...

    async def get_all(self, server_id: int) -> SettingOptions: ...

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None: ...

    async def on_guild_join(self, server_id: int) -> None: ...

    async def on_guild_remove(self, server_id: int) -> None: ...
