"""Some functions related to storing and changing server ids for sending records."""
from Database.database import DatabaseManager
from Discord.config import SETTABLE_CHANNELS_TYPE, SETTABLE_CHANNELS

# The names of the settings in the database, mapped from the channel purpose,
# which is the name of the setting in the UI.
PURPOSE_TO_SETTING = {'Smallest': 'smallest_channel_id',
                      'Fastest': 'fastest_channel_id',
                      'First': 'first_channel_id',
                      'Builds': 'builds_channel_id',
                      'Vote': 'voting_channel_id'}
# TODO: inconsistent naming
SETTING_TO_PURPOSE = {value: key for key, value in PURPOSE_TO_SETTING.items()}
assert len(PURPOSE_TO_SETTING) == len(SETTING_TO_PURPOSE), 'The mapping is not bijective!'
assert set(PURPOSE_TO_SETTING.keys()) == set(SETTABLE_CHANNELS), 'The mapping is not exhaustive!'

def get_setting_name(channel_purpose: str) -> str:
    """Maps a channel purpose to the column name in the database."""
    return PURPOSE_TO_SETTING[channel_purpose]

def get_purpose_name(setting_name: str) -> str:
    """Maps a column name in the database to the channel purpose."""
    return SETTING_TO_PURPOSE[setting_name]

def get_server_setting(server_id: int, channel_purpose: SETTABLE_CHANNELS_TYPE) -> int | None:
    """Gets the channel id of the specified purpose for a server."""
    setting_name = get_setting_name(channel_purpose)
    response = DatabaseManager().table('server_settings').select(setting_name, count='exact').eq('server_id',
                                                                                                 server_id).maybe_single().execute()
    return response.data[setting_name] if response.count > 0 else None

def get_server_settings(server_id: int) -> dict[str, int]:
    """Gets a list of settings for a server."""
    response = DatabaseManager().table('server_settings').select('*', count='exact').eq('server_id',
                                                                                        server_id).maybe_single().execute()
    if response.count == 0:
        return {}

    settings = response.data
    return {get_purpose_name(setting_name): value for setting_name, value in settings.items() if setting_name != 'server_id'}


def update_server_setting(server_id: int, channel_purpose: SETTABLE_CHANNELS_TYPE, value: int | None) -> None:
    """Updates a setting for a server."""
    setting_name = get_setting_name(channel_purpose)
    DatabaseManager().table('server_settings').upsert({'server_id': server_id, setting_name: value}).execute()


def update_server_settings(server_id: int, channel_purposes: dict[SETTABLE_CHANNELS_TYPE, int]) -> None:
    """Updates a list of settings for a server."""
    settings = {get_setting_name(purpose): value for purpose, value in channel_purposes.items()}
    DatabaseManager().table('server_settings').upsert({'server_id': server_id, **settings}).execute()
