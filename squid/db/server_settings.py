"""Some functions related to storing and changing server ids for sending records."""

from collections.abc import Iterable
from typing import Literal, overload

from postgrest.base_request_builder import APIResponse, SingleAPIResponse
from postgrest.types import CountMethod, ReturnMethod

from squid.db.schema import (
    SETTINGS,
    DbSettingKey,
    ServerSettingRecord,
    Setting,
)
from supabase import AsyncClient

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

    def __init__(self, client: AsyncClient):
        self.client = client

    @overload
    async def get(
        self, server_ids: Iterable[int], setting: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
    ) -> dict[int, int | None]: ...
    @overload
    async def get(self, server_ids: Iterable[int], setting: Literal["Staff", "Trusted"]) -> dict[int, list[int]]: ...

    # pyright cannot infer that the overloads are actually compatible with the definition below even though we used proper TypedDicts
    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:  # type: ignore
        """Gets the settings for a list of servers."""
        col_name = _SETTING_TO_DB_KEY[setting]
        response: APIResponse[ServerSettingRecord] = (
            await self.client.table("server_settings")
            .select("server_id", col_name)
            .in_("server_id", server_ids)
            .execute()
        )
        return {record["server_id"]: record[col_name] for record in response.data}

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
        col_name = _SETTING_TO_DB_KEY[setting]
        response: SingleAPIResponse[ServerSettingRecord] | None = (
            await self.client.table("server_settings")
            .select(col_name, count=CountMethod.exact)
            .eq("server_id", server_id)
            .maybe_single()
            .execute()
        )
        if response is None:
            return None
        return response.data.get(col_name)

    async def get_all(self, server_id: int) -> dict[Setting, int | list[int] | None]:
        """Gets the settings for a server."""
        response: SingleAPIResponse[ServerSettingRecord] | None = (
            await self.client.table("server_settings").select("*").eq("server_id", server_id).maybe_single().execute()
        )
        if response is None:
            return {}

        settings = response.data

        excluded_columns = ["server_id", "in_server"]
        return {
            _DB_KEY_TO_SETTING[setting_name]: id  # type: ignore
            for setting_name, id in settings.items()
            if setting_name not in excluded_columns
        }  # type: ignore

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
        col_name = _SETTING_TO_DB_KEY[setting]
        await (
            self.client.table("server_settings")
            .upsert({"server_id": server_id, col_name: value}, returning=ReturnMethod.minimal)
            .execute()
        )

    async def set_all(self, server_id: int, settings: dict[Setting, int | list[int] | None]) -> None:
        """Updates a list of settings for a server."""
        data = {_SETTING_TO_DB_KEY[setting]: value for setting, value in settings.items()}
        await (
            self.client.table("server_settings")
            .upsert({"server_id": server_id, **data}, returning=ReturnMethod.minimal)
            .execute()
        )
