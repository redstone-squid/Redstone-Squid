"""Some functions related to storing and changing server ids for sending records."""

from collections.abc import Iterable
from typing import Literal, overload

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import (
    SETTINGS,
    DbSettingKey,
    ServerSetting,
    Setting,
)

# Mapping of settings to the column names in the database.
# This file should be the only place that is aware of the database column names.
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
assert set(_SETTING_TO_DB_KEY.keys()) == set(SETTINGS), "The mapping is not exhaustive!"


class ServerSettingManager:
    """A class for managing server setting."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    @overload
    async def get(
        self, server_ids: Iterable[int], setting: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
    ) -> dict[int, int | None]: ...
    @overload
    async def get(self, server_ids: Iterable[int], setting: Literal["Staff", "Trusted"]) -> dict[int, list[int]]: ...

    # pyright cannot infer that the overloads are actually compatible with the definition below even though we used proper TypedDicts
    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:  # type: ignore
        """Gets the settings for a list of servers."""
        async with self.session() as session:
            col_name = _SETTING_TO_DB_KEY[setting]
            stmt = select(ServerSetting).where(ServerSetting.server_id.in_(server_ids))
            result = await session.execute(stmt)
            settings = result.scalars().all()
            return {setting.server_id: getattr(setting, col_name) for setting in settings}

    @overload
    async def get_single(
        self, server_id: int, setting: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
    ) -> int | None: ...
    @overload
    async def get_single(self, server_id: int, setting: Literal["Staff", "Trusted"]) -> list[int]: ...
    @overload
    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None: ...

    # noinspection PyTypedDict
    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        """
        Gets a channel id or role list id for a server depending on the type of setting.

        The returned channel ids are always a ``GuildMessageable``.
        """
        async with self.session() as session:
            col_name = _SETTING_TO_DB_KEY[setting]
            stmt = select(ServerSetting).where(ServerSetting.server_id == server_id)
            result = await session.execute(stmt)
            setting_obj = result.scalar_one_or_none()
            if setting_obj is None:
                return None
            return getattr(setting_obj, col_name)

    async def get_all(self, server_id: int) -> dict[Setting, int | list[int] | None]:
        """Gets the settings for a server."""
        async with self.session() as session:
            stmt = select(ServerSetting).where(ServerSetting.server_id == server_id)
            result = await session.execute(stmt)
            setting_obj = result.scalar_one_or_none()
            if setting_obj is None:
                return {}

            return {
                _DB_KEY_TO_SETTING[setting_name]: getattr(setting_obj, setting_name)
                for setting_name in _DB_KEY_TO_SETTING.keys()
            }

    @overload
    async def set(
        self, server_id: int, setting: Literal["Smallest", "Fastest", "First", "Builds", "Vote"], value: int | None
    ) -> None: ...
    @overload
    async def set(self, server_id: int, setting: Literal["Staff", "Trusted"], value: list[int] | None) -> None: ...
    @overload
    async def set(self, server_id: int, setting: Setting, value: int | list[int] | None) -> None: ...

    async def set(self, server_id: int, setting: Setting, value: int | list[int] | None) -> None:
        """Updates a setting for a server."""
        async with self.session() as session:
            col_name = _SETTING_TO_DB_KEY[setting]
            stmt = select(ServerSetting).where(ServerSetting.server_id == server_id)
            result = await session.execute(stmt)
            setting_obj = result.scalar_one_or_none()

            if setting_obj is None:
                setting_obj = ServerSetting(server_id=server_id)
                session.add(setting_obj)

            setattr(setting_obj, col_name, value)
            await session.commit()

    async def set_all(self, server_id: int, settings: dict[Setting, int | list[int] | None]) -> None:
        """Updates a list of settings for a server."""
        async with self.session() as session:
            stmt = select(ServerSetting).where(ServerSetting.server_id == server_id)
            result = await session.execute(stmt)
            setting_obj = result.scalar_one_or_none()

            if setting_obj is None:
                setting_obj = ServerSetting(server_id=server_id)
                session.add(setting_obj)

            for setting, value in settings.items():
                col_name = _SETTING_TO_DB_KEY[setting]
                setattr(setting_obj, col_name, value)

            await session.commit()
