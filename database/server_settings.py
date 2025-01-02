"""Some functions related to storing and changing server ids for sending records."""

from postgrest.base_request_builder import SingleAPIResponse
from postgrest.types import CountMethod
from typing_extensions import overload

from database import DatabaseManager
from database.schema import (
    ServerSettingRecord,
    DbSettingKey,
    ChannelPurpose,
    RoleSetting,
    Setting,
    CHANNEL_PURPOSES,
    SETTINGS,
)

from typing import List, Any

CHANNEL_PURPOSE_TO_DB_SETTING: dict[ChannelPurpose, DbSettingKey] = {
    "Smallest": "smallest_channel_id",
    "Fastest": "fastest_channel_id",
    "First": "first_channel_id",
    "Builds": "builds_channel_id",
    "Vote": "voting_channel_id",
}

ROLE_SETTING_TO_DB_SETTING: dict[RoleSetting, DbSettingKey] = {
    "Staff": "staff_roles_ids",
    "Trusted": "trusted_roles_ids",
}

SETTING_TO_DB_SETTING: dict[Setting, DbSettingKey] = {
    **CHANNEL_PURPOSE_TO_DB_SETTING,
    **ROLE_SETTING_TO_DB_SETTING,
}

DB_SETTING_TO_SETTING: dict[DbSettingKey, Setting] = {value: key for key, value in SETTING_TO_DB_SETTING.items()}
assert set(SETTING_TO_DB_SETTING.keys()) == set(SETTINGS), "The mapping is not exhaustive!"


def get_setting_name(setting: Setting) -> DbSettingKey:
    """Maps a setting to the column name in the database."""
    return SETTING_TO_DB_SETTING[setting]


def get_purpose_name(setting_name: DbSettingKey) -> Setting:
    """Maps a column name in the database to the setting."""
    return DB_SETTING_TO_SETTING[setting_name]


# async def get_server_channel_purpose(server_id: int, channel_purpose: ChannelPurpose) -> int | None:
#     """Gets the channel id of the specified purpose for a server. The channels fetched are always GuildMessageable unless the server admins changed them."""
#     setting_name = get_setting_name(channel_purpose)
#     response: SingleAPIResponse[ServerSettingRecord] | None = (
#         await DatabaseManager()
#         .table("server_settings")
#         .select(setting_name, count=CountMethod.exact)
#         .eq("server_id", server_id)
#         .maybe_single()
#         .execute()
#     )
#     if response is None:
#         return None
#     return response.data.get(setting_name)


@overload
async def get_server_setting(server_id: int, setting: ChannelPurpose) -> int | None: ...
@overload
async def get_server_setting(server_id: int, setting: RoleSetting) -> list[int] | None: ...


async def get_server_setting(server_id: int, setting: Setting) -> int | list[int] | None:
    """
    Gets a channel id or role list id for a server depending on the type of setting.

    The returned channel ids are always a ``GuildMessageable``.
    """
    setting_name = get_setting_name(setting)
    response: SingleAPIResponse[ServerSettingRecord] | None = (
        await DatabaseManager()
        .table("server_settings")
        .select(setting_name, count=CountMethod.exact)
        .eq("server_id", server_id)
        .maybe_single()
        .execute()
    )
    if response is None:
        return None
    return response.data.get(setting_name)


async def get_server_settings(server_id: int) -> dict[Setting, int | list[int]]:
    """Gets the settings for a server."""
    response: SingleAPIResponse[ServerSettingRecord] | None = (
        await DatabaseManager().table("server_settings").select("*").eq("server_id", server_id).maybe_single().execute()
    )
    if response is None:
        return {}

    settings = response.data
    return {get_purpose_name(setting_name): id for setting_name, id in settings.items() if setting_name != "server_id"}  # type: ignore


async def update_server_setting(server_id: int, setting: Setting, value: int | list[int] | None) -> None:
    """Updates a setting for a server."""
    setting_name = get_setting_name(setting)
    await DatabaseManager().table("server_settings").update({"server_id": server_id, setting_name: value}).execute()


async def update_server_settings(server_id: int, settings: dict[Setting, int | list[int] | None]) -> None:
    """Updates a list of settings for a server."""
    settings = {get_setting_name(purpose): value for purpose, value in settings.items()}
    await DatabaseManager().table("server_settings").update({"server_id": server_id, **settings}).execute()
