"""Server settings application services."""

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import cast

from squid.core.actors import ActingContext, application_operation
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.core.settings.application.discord import (
    DiscordChannel,
    DiscordRole,
    RuntimeDiscordReferences,
)
from squid.core.settings.application.edits import guild_snapshot
from squid.core.settings.application.ports import GuildSettingsUnitOfWork
from squid.core.settings.domain import Setting, SettingOptions
from squid.core.settings.domain.guild import CHANNEL_FIELDS, GuildSettings
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.unit_of_work import UnitOfWorkFactory


class SettingsService:
    """Read and update server settings in short transactions."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[GuildSettingsUnitOfWork],
        permissions: PermissionService,
        runtime_defaults: RuntimeSettings | None = None,
        discord: RuntimeDiscordReferences | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._permissions = permissions
        self._runtime_defaults = runtime_defaults or RuntimeSettings()
        self._discord = discord or RuntimeDiscordReferences(None, owner_guild_id=None)

    @application_operation
    async def get_runtime(self, *, context: ActingContext) -> RuntimeSettings:
        """Read current settings, persisting initial defaults on first use."""
        async with self._uow_factory() as uow:
            row = await uow.settings.runtime(self._runtime_defaults)
            result = RuntimeSettings.model_validate(row)
            await uow.commit()
            return result

    @application_operation
    async def runtime_snapshot(self, *, context: ActingContext, for_edit: bool = False) -> tuple[RuntimeSettings, int]:
        """Read a settings revision for inspection or the result of an authorized edit."""
        await self._permissions.require(context.effective, SETTINGS_RUNTIME_EDIT if for_edit else SETTINGS_RUNTIME_VIEW)
        async with self._uow_factory() as uow:
            row = await uow.settings.runtime(self._runtime_defaults)
            result = RuntimeSettings.model_validate(row), row.revision
            await uow.commit()
            return result

    @application_operation
    async def runtime_names(self, settings: RuntimeSettings, *, context: ActingContext) -> Mapping[int, str] | None:
        """Name the Discord IDs in `settings`, or return None when Discord is unavailable.

        An ID missing from the result is one Discord no longer knows, or the bot cannot see.
        """
        await self._permissions.require(context.effective, SETTINGS_RUNTIME_VIEW)
        return await self._discord.names(settings)

    @application_operation
    async def owner_guild_channels(self, *, context: ActingContext) -> Sequence[DiscordChannel]:
        """List the channels runtime settings can pick from. Raises DiscordUnavailableError."""
        await self._permissions.require(context.effective, SETTINGS_RUNTIME_VIEW)
        return await self._discord.channels()

    @application_operation
    async def owner_guild_roles(self, *, context: ActingContext) -> Sequence[DiscordRole]:
        """List the roles runtime settings can pick from. Raises DiscordUnavailableError."""
        await self._permissions.require(context.effective, SETTINGS_RUNTIME_VIEW)
        return await self._discord.roles()

    @application_operation
    async def guild_snapshot(self, guild_id: int, *, context: ActingContext) -> tuple[GuildSettings, int]:
        async with self._uow_factory() as uow:
            result = await guild_snapshot(uow, guild_id)
            await uow.commit()
            return result

    @application_operation
    async def guild_joined(self, server_id: int, *, context: ActingContext) -> None:
        """Mark the bot as present, creating settings if this is its first visit."""
        async with self._uow_factory() as uow:
            settings = await uow.settings.get_or_create_for_update(server_id)
            settings.in_server = True
            await uow.commit()

    @application_operation
    async def guild_removed(self, server_id: int, *, context: ActingContext) -> None:
        """Mark the bot as absent while retaining settings for a later rejoin."""
        async with self._uow_factory() as uow:
            settings = await uow.settings.get(server_id, for_update=True)
            if settings is not None:
                settings.in_server = False
                await uow.commit()

    @application_operation
    async def sync_presence(
        self, present_server_ids: Iterable[int], *, context: ActingContext
    ) -> tuple[Sequence[int], Sequence[int]]:
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

    @application_operation
    async def referenced_channels(self, server_id: int, *, context: ActingContext) -> frozenset[int]:
        """Return the channel IDs this guild's settings point at."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            if row is None:
                return frozenset()
            return frozenset(
                channel_id for column in CHANNEL_FIELDS.values() if (channel_id := getattr(row, column)) is not None
            )

    @application_operation
    async def forget_channels(
        self, server_id: int, channel_ids: Collection[int], *, context: ActingContext
    ) -> tuple[Setting, ...]:
        """Clear every channel setting that points at one of the given deleted channels.

        Returns:
            The settings that were cleared.
        """
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id, for_update=True)
            if row is None:
                return ()
            cleared: tuple[Setting, ...] = tuple(
                setting for setting, column in CHANNEL_FIELDS.items() if getattr(row, column) in channel_ids
            )
            for setting in cleared:
                setattr(row, CHANNEL_FIELDS[setting], None)
            await uow.commit()
            return cleared

    @application_operation
    async def get_many(
        self, server_ids: Iterable[int], setting: Setting, *, context: ActingContext
    ) -> Mapping[int, int | None]:
        """Return the requested channel setting, omitting servers without settings."""
        async with self._uow_factory() as uow:
            rows = await uow.settings.get_many(server_ids)
            return {row.server_id: cast(int | None, getattr(row, CHANNEL_FIELDS[setting])) for row in rows}

    @application_operation
    async def get(self, server_id: int, setting: Setting, *, context: ActingContext) -> int | None:
        """Return a channel ID, or None if the setting or server is missing."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            return None if row is None else cast(int | None, getattr(row, CHANNEL_FIELDS[setting]))

    @application_operation
    async def get_all(self, server_id: int, *, context: ActingContext) -> SettingOptions:
        """Return every channel setting, or an empty mapping for an unknown server."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            if row is None:
                return {}
            return SettingOptions(**{setting: getattr(row, column) for setting, column in CHANNEL_FIELDS.items()})

    @application_operation
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        """Get the server's admin-configured locale override, if any."""
        async with self._uow_factory() as uow:
            row = await uow.settings.get(server_id)
            return None if row is None else row.locale
