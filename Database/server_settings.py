from Database.database import DatabaseManager

def get_server_setting(server_id: int, setting_name: str) -> int | None:
    """Gets a setting for a server."""
    db = DatabaseManager()
    response = db.table('server_settings').select(setting_name, count='exact').eq('server_id', server_id).maybe_single().execute()
    return response.data[setting_name] if response.count > 0 else None

def get_server_settings(server_id: int) -> dict[str, int]:
    """Gets a list of settings for a server."""
    db = DatabaseManager()
    response = db.table('server_settings').select('*', count='exact').eq('server_id', server_id).maybe_single().execute()
    return response.data if response.count > 0 else {}

def update_server_setting(server_id: int, setting_name: str, value: int | None) -> None:
    """Updates a setting for a server."""
    # FIXME: handle postgrest.exceptions.APIError when invalid setting_name is passed
    DatabaseManager().table('server_settings').upsert({'server_id': server_id, setting_name: value}).execute()

def update_server_settings(server_id: int, settings_dict: dict[str, int]) -> None:
    """Updates a list of settings for a server."""
    DatabaseManager().table('server_settings').upsert({'server_id': server_id, **settings_dict}).execute()
