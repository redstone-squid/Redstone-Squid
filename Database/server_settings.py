"""Some functions related to storing and changing server ids for sending records."""

from postgrest.base_request_builder import SingleAPIResponse
from postgrest.types import CountMethod

from Database.database import DatabaseManager
from Database.schema import ServerSettingRecord, DbSettingKey, ChanelPurpose, CHANNEL_PURPOSES

# The names of the settings in the database, mapped from the channel purpose,
# which is the name of the setting in the UI.
PURPOSE_TO_SETTING: dict[ChanelPurpose, DbSettingKey] = {
    "Smallest": "smallest_channel_id",
    "Fastest": "fastest_channel_id",
    "First": "first_channel_id",
    "Builds": "builds_channel_id",
    "Vote": "voting_channel_id",
}
# TODO: inconsistent naming
SETTING_TO_PURPOSE: dict[DbSettingKey, ChanelPurpose] = {
    value: key for key, value in PURPOSE_TO_SETTING.items()
}
assert set(PURPOSE_TO_SETTING.keys()) == set(CHANNEL_PURPOSES), "The mapping is not exhaustive!"


def get_setting_name(channel_purpose: ChanelPurpose) -> DbSettingKey:
    """Maps a channel purpose to the column name in the database."""
    return PURPOSE_TO_SETTING[channel_purpose]


def get_purpose_name(setting_name: DbSettingKey) -> ChanelPurpose:
    """Maps a column name in the database to the channel purpose."""
    return SETTING_TO_PURPOSE[setting_name]


async def get_server_setting(server_id: int, channel_purpose: ChanelPurpose) -> int | None:
    """Gets the channel id of the specified purpose for a server."""
    setting_name = get_setting_name(channel_purpose)
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


async def get_server_settings(server_id: int) -> dict[ChanelPurpose, int]:
    """Gets the settings for a server."""
    response: SingleAPIResponse[ServerSettingRecord] | None = (
        await DatabaseManager().table("server_settings").select("*").eq("server_id", server_id).maybe_single().execute()
    )
    if response is None:
        return {}

    settings = response.data
    return {get_purpose_name(setting_name): id for setting_name, id in settings.items() if setting_name != "server_id"}  # type: ignore


async def update_server_setting(server_id: int, channel_purpose: ChanelPurpose, value: int | None) -> None:
    """Updates a setting for a server."""
    setting_name = get_setting_name(channel_purpose)
    await DatabaseManager().table("server_settings").upsert({"server_id": server_id, setting_name: value}).execute()


async def update_server_settings(server_id: int, channel_purposes: dict[ChanelPurpose, int | None]) -> None:
    """Updates a list of settings for a server."""
    settings = {get_setting_name(purpose): value for purpose, value in channel_purposes.items()}
    await DatabaseManager().table("server_settings").upsert({"server_id": server_id, **settings}).execute()
