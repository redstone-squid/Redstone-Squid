import Database.main as DB

# Adds new server to database. Returns the row index of the new record.
def add_new_server(server_id: int):
    wks = DB.get_server_settings_worksheet()
    wks.append_row([str(server_id)])

    return wks.row_count + 1

# Gets the row in the database which contains the server settings.
def get_server_row_index(server_id: int) -> int | None:
    wks = DB.get_server_settings_worksheet()
    records = wks.get_all_records()

    # Finding the row which contains the correct server id.
    for index in range(len(records)):
        if int(records[index]['Server ID']) == server_id:
            return index + 1

    return None

def get_server_row_number(server_id: int) -> int | None:
    index = get_server_row_index(server_id)
    if index is None:
        return None
    return index + 1

# Get a setting for a server.
def get_server_setting(server_id: int, setting_name: str) -> int | None:
    settings = get_server_settings(server_id)
    if settings is None:
        return
    return get_server_settings(server_id)[setting_name]

# Gets a list of settings for a server.
def get_server_settings(server_id: int) -> dict[str, int] | None:
    # Opening the server settings worksheet.
    wks = DB.get_server_settings_worksheet()
    records = wks.get_all_records()

    for record in records:
        if int(record['Server ID']) == server_id:
            return {key: int(value) for key, value in record.items() if key != 'Server ID' and value}

    return None

# Updates a setting for a server.
def update_server_setting(server_id: int, setting_name: str, value: int) -> None:
    # Opening the server settings worksheet.
    wks = DB.get_server_settings_worksheet()

    row = get_server_row_number(server_id)
    # Adding server to database if it isn't already present.
    if row is None:
        row = add_new_server(server_id)

    col = DB.get_col_number(wks, setting_name)

    # Throwing exception if setting doesn't exist.
    if col is None:
        raise Exception(f'No settings called {setting_name}.')

    wks.update_cell(row, col, value)

# Updates multiple settings for a server.
def update_server_settings(server_id: int, settings_dict: dict[str, int]) -> None:
    wks = DB.get_server_settings_worksheet()

    row_index = get_server_row_number(server_id)
    # Adding server to database if it isn't already present.
    if row_index is None:
        row_index = add_new_server(server_id)

    row = wks.row_values(row_index)

    # Making row the correct length
    length = wks.col_count
    while len(row) < length:
        row.append(None)

    # For each setting, update the setting.
    for key, value in settings_dict.items():
        col = DB.get_col_index(wks, key)

        if col is None:
            raise Exception(f'No settings called {key}.')

        row[col] = value

    # Writing to cells.
    cells_to_update = wks.range(row_index, 1, row_index, len(row))
    for i, val in enumerate(row):
        cells_to_update[i].value = str(val)

    # Updating cells.
    wks.update_cells(cells_to_update)