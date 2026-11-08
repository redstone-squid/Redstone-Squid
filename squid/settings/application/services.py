"""Server settings application services."""

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import cast

from squid.settings.application.ports import SettingsUnitOfWork
from squid.settings.domain import ScalarChannelSetting, Setting, SettingOptions
from squid.unit_of_work import UnitOfWorkFactory

_CHANNEL_FIELDS: dict[Setting, str] = {
    "Smallest": "smallest_channel_id",
    "Fastest": "fastest_channel_id",
    "First": "first_channel_id",
    "Builds": "builds_channel_id",
    "Vote": "voting_channel_id",
}


class SettingsService:
    """Read and update server settings in short transactions."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[SettingsUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def guild_joined(self, server_id: int) -> None:
        """Mark the bot as present, creating settings if this is its first visit."""
        async with self._uow_factory() as uow:
            settings = await uow.settings.get_or_create_for_update(server_id)
            settings.in_server = True
            await uow.commit()

    async def guild_removed(self, server_id: int) -> None:
        """Mark the bot as absent while retaining settings for a later rejoin."""
        async with self._uow_factory() as uow:
            settings = await uow.settings.get(server_id, for_update=True)
            if settings is not None:
                settings.in_server = False
                await uow.commit()

    async def sync_presence(self, present_server_ids: Iterable[int]) -> tuple[Sequence[int], Sequence[int]]:
        """Mark exactly the given guilds as present, creating settings for any not seen before.

        This repairs `guild_joined` and `guild_removed` events missed while the bot was offline.
        Pass every guild the bot is currently in: any guild not listed is marked absent.

        Returns:
            The guilds that became present and the guilds that became absent.
        """
        async with self._uow_factory() as uow:
            changes = await uow.settings.sync_presence(frozenset(present_server_ids))
            await uow.commit()
            return changes

    async def referenced_channels(self, server_id: int) -> frozenset[int]:
        """Return the channel IDs this guild's settings point at."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            if row is None:
                return frozenset()
            return frozenset(
                channel_id for column in _CHANNEL_FIELDS.values() if (channel_id := getattr(row, column)) is not None
            )

    async def forget_channels(self, server_id: int, channel_ids: Collection[int]) -> tuple[Setting, ...]:
        """Clear every channel setting that points at one of the given deleted channels.

        Returns:
            The settings that were cleared.
        """
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id, for_update=True)
            if row is None:
                return ()
            cleared = tuple(
                setting for setting, column in _CHANNEL_FIELDS.items() if getattr(row, column) in channel_ids
            )
            for setting in cleared:
                setattr(row, _CHANNEL_FIELDS[setting], None)
            await uow.commit()
            return cleared

    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | None]:
        """Return the requested channel setting, omitting servers without settings."""
        async with self._uow_factory() as uow:
            rows = await uow.settings.get_many(server_ids)
            return {row.server_id: cast(int | None, getattr(row, _CHANNEL_FIELDS[setting])) for row in rows}

    async def get(self, server_id: int, setting: Setting) -> int | None:
        """Return a channel ID, or None if the setting or server is missing."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            return None if row is None else cast(int | None, getattr(row, _CHANNEL_FIELDS[setting]))

    async def get_all(self, server_id: int) -> SettingOptions:
        """Return every channel setting, or an empty mapping for an unknown server."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            if row is None:
                return {}
            return SettingOptions(**{setting: getattr(row, column) for setting, column in _CHANNEL_FIELDS.items()})

    async def set_channel(self, server_id: int, setting: ScalarChannelSetting, channel_id: int) -> None:
        """Save a channel ID; the caller must check that it is usable in this server."""
        await self._set(server_id, setting, channel_id)

    async def clear(self, server_id: int, setting: Setting) -> None:
        """Unset a channel destination, creating the settings row if needed."""
        await self._set(server_id, setting, None)

    async def get_locale(self, server_id: int) -> str | None:
        """Get the server's admin-configured locale override, if any."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            return None if row is None else row.locale

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        """Set or clear the server's admin-configured locale override."""
        async with self._uow_factory() as uow:
            settings = await uow.settings.get_or_create_for_update(server_id)
            settings.locale = locale
            await uow.commit()

    async def _set(self, server_id: int, setting: Setting, value: int | None) -> None:
        async with self._uow_factory() as uow:
            settings = await uow.settings.get_or_create_for_update(server_id)
            setattr(settings, _CHANNEL_FIELDS[setting], value)
            await uow.commit()
