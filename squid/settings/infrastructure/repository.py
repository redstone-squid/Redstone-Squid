"""SQLAlchemy server settings repository."""

from collections.abc import Iterable
from typing import Literal, Unpack, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._model_repos import ServerSettingModelRepository
from squid.db.schema import ServerSetting
from squid.settings.domain import Setting, SettingOptions

DbSettingKey = Literal[
    "smallest_channel_id",
    "fastest_channel_id",
    "first_channel_id",
    "builds_channel_id",
    "voting_channel_id",
    "staff_roles_ids",
    "trusted_roles_ids",
]
_SETTING_TO_DB_KEY: dict[Setting, DbSettingKey] = {
    "Smallest": "smallest_channel_id",
    "Fastest": "fastest_channel_id",
    "First": "first_channel_id",
    "Builds": "builds_channel_id",
    "Vote": "voting_channel_id",
    "Staff": "staff_roles_ids",
    "Trusted": "trusted_roles_ids",
}
_DB_KEY_TO_SETTING: dict[DbSettingKey, Setting] = {value: key for key, value in _SETTING_TO_DB_KEY.items()}


class SettingsRepository:
    """Persist server settings for application services."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def get(
        self,
        server_ids: Iterable[int],
        setting: Setting,
    ) -> dict[int, int | list[int] | None]:
        """Get a single setting's value for each of the given servers.

        Args:
            server_ids: The server IDs to look up.
            setting: The setting to read.

        Returns:
            A mapping of server ID to that server's value for *setting*.
            Servers with no row are omitted.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session)
            rows = await repository.get_many(ServerSetting.server_id.in_(tuple(server_ids)))
            column_name = _SETTING_TO_DB_KEY[setting]
            return {row.server_id: cast(int | list[int] | None, getattr(row, column_name)) for row in rows}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        """Get a single setting's value for one server.

        Args:
            server_id: The server ID to look up.
            setting: The setting to read.

        Returns:
            The server's value for *setting*, or None if the server has no row
            or the setting is unset.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is None:
                return None
            return cast(int | list[int] | None, getattr(row, _SETTING_TO_DB_KEY[setting]))

    async def get_all(self, server_id: int) -> SettingOptions:
        """Get every setting for one server.

        Args:
            server_id: The server ID to look up.

        Returns:
            All settings for the server, or an empty mapping if the server has
            no row.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is None:
                return {}
            return SettingOptions(
                **{_DB_KEY_TO_SETTING[column_name]: getattr(row, column_name) for column_name in _DB_KEY_TO_SETTING}
            )

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        """Set one or more settings for a server, creating its row if needed.

        Args:
            server_id: The server ID to update.
            **settings: The settings to set, by name.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session, auto_commit=True)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is None:
                row = ServerSetting(server_id=server_id)
                for setting, value in settings.items():
                    setattr(row, _SETTING_TO_DB_KEY[cast(Setting, setting)], value)
                await repository.add(row)
                return
            for setting, value in settings.items():
                setattr(row, _SETTING_TO_DB_KEY[cast(Setting, setting)], value)
            await repository.update(row)

    async def on_guild_join(self, server_id: int) -> None:
        """Mark a server as joined, creating its settings row if needed.

        Args:
            server_id: The server ID that was joined.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session, auto_commit=True)
            await repository.get_or_upsert(
                match_fields="server_id",
                server_id=server_id,
                in_server=True,
            )

    async def on_guild_remove(self, server_id: int) -> None:
        """Mark a server as no longer joined.

        Args:
            server_id: The server ID that was removed.
        """
        async with self._session_factory() as session:
            repository = ServerSettingModelRepository(session=session, auto_commit=True)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is not None:
                row.in_server = False
                await repository.update(row)
