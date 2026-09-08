"""Server settings application ports."""

from collections.abc import Iterable
from typing import Protocol, Unpack

from squid.settings.domain import Setting, SettingOptions


class SettingsStore(Protocol):
    """Persistence operations required by `SettingsService`."""

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | None]:
        """One setting across several servers; servers with no settings row are absent from the mapping."""
        ...

    async def get_single(self, server_id: int, setting: Setting) -> int | None:
        """One setting's value, or `None` if it is unset or the server has no row."""
        ...

    async def get_all(self, server_id: int) -> SettingOptions:
        """Every setting for the server, or an empty mapping if it has no row."""
        ...

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        """Write the named settings, creating the server's row if needed and leaving unnamed settings alone."""
        ...

    async def get_locale(self, server_id: int) -> str | None:
        """The admin-configured locale override, or `None` when the server follows Discord's locale."""
        ...

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        """Set the locale override, or clear it with `None`, creating the server's row if needed."""
        ...

    async def on_guild_join(self, server_id: int) -> None:
        """Mark the server as joined, creating its row if this is the first time the bot has seen it."""
        ...

    async def on_guild_remove(self, server_id: int) -> None:
        """Mark the server as no longer joined, keeping its settings for a later rejoin."""
        ...
