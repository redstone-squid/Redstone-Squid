"""Advanced-alchemy repository for server settings."""

from collections.abc import Iterable
from typing import Unpack, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._base import BaseAsyncRepository
from squid.db.schema import ServerSetting, Setting
from squid.db.server_settings import DbSettingKey, SettingOptions

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


class _ServerSettingModelRepository(BaseAsyncRepository[ServerSetting]):
    model_type = ServerSetting


class SettingsRepository:
    """Persist server settings for application services."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def get(
        self,
        server_ids: Iterable[int],
        setting: Setting,
    ) -> dict[int, int | list[int] | None]:
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session)
            rows = await repository.list(ServerSetting.server_id.in_(tuple(server_ids)))
            column_name = _SETTING_TO_DB_KEY[setting]
            return {row.server_id: cast(int | list[int] | None, getattr(row, column_name)) for row in rows}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is None:
                return None
            return cast(int | list[int] | None, getattr(row, _SETTING_TO_DB_KEY[setting]))

    async def get_all(self, server_id: int) -> SettingOptions:
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is None:
                return {}
            return SettingOptions(
                **{_DB_KEY_TO_SETTING[column_name]: getattr(row, column_name) for column_name in _DB_KEY_TO_SETTING}
            )

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session, auto_commit=True)
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
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session, auto_commit=True)
            await repository.get_or_upsert(
                match_fields="server_id",
                server_id=server_id,
                in_server=True,
            )

    async def on_guild_remove(self, server_id: int) -> None:
        async with self._session_factory() as session:
            repository = _ServerSettingModelRepository(session=session, auto_commit=True)
            row = await repository.get_one_or_none(server_id=server_id)
            if row is not None:
                row.in_server = False
                await repository.update(row)
