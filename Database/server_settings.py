import Database.main as DB
import Database.config as config
import Google.interface as Google

# Get the worksheet which contains the server settings.
def get_server_settings_worksheet():
    return DB.get_workbook().get_worksheet(config.SERVER_SETTINGS_SHEET_INDEX)

# Adds new server to database. Returns the row index of the new record.
def add_new_server(server_id):
    wks = get_server_settings_worksheet()
    wks.append_row([server_id])

    return wks.row_count + 1

# Returns a list of possible settings.
def settings_list():
    wks = get_server_settings_worksheet()
    return wks.row_values(1)

# Returns the amount of settings a server can have.
def setting_count():
    return len(settings_list())

# Gets the row in the database which contains the server settings.
def get_server_row_index(server_id):
    wks = get_server_settings_worksheet()
    records = wks.get_all_records()

    # Finding the row which contains the correct server id.
    for index in range(len(records)):
        if int(records[index]['Server ID']) == int(server_id):
            return index + 1
    
    return None

def get_server_row_number(server_id):
    index = get_server_row_index(server_id)
    return None if index == None else index + 1 

# Gets the column in the database which contains the setting.
def get_setting_col_index(setting_name):
    wks = get_server_settings_worksheet()
    headers = wks.row_values(1)

    # Finding the column which contains the correct setting name.
    try:
        return headers.index(setting_name)
    except ValueError:
        return None

def get_setting_col_number(setting_name):
    index = get_setting_col_index(setting_name)
    return None if index == None else index + 1

# Get a setting for a server.
def get_server_setting(server_id, setting_name):
    settings = get_server_settings(server_id)
    if settings == None:
        return None
    return get_server_settings(server_id)[setting_name]

# Gets a list of settings for a server.
def get_server_settings(server_id):
    # Opening the server settings worksheet.
    wks = get_server_settings_worksheet()
    records = wks.get_all_records()

    for server in records:
        if int(server['Server ID']) == int(server_id):
            return server

    return None

# Updates a setting for a server.
def update_server_setting(server_id, setting_name, value):
    # Opening the server settings worksheet.
    wks = get_server_settings_worksheet()
    
    row = get_server_row_number(server_id)
    # Adding server to database if it isnt already present.
    if row == None:
        row = add_new_server(server_id)

    col = get_setting_col_number(setting_name)
    
    # Throwing exception if setting doesn't exist.
    if col == None:
        raise Exception('No settings called {}.'.format(setting_name))
    
    wks.update_cell(row, col, value)

# Updates multiple settings for a server.
def update_server_settings(server_id, settings_dict):
    wks = get_server_settings_worksheet()
    
    row_index = get_server_row_number(server_id)
    # Adding server to database if it isnt already present.
    if row_index == None:
        row_index = add_new_server(server_id)

    row = wks.row_values(row_index)

    # Making row the correct length
    length = setting_count()
    while len(row) < length:
        row.append(None)

    # For each setting, update the setting.
    for key, value in settings_dict.items():
        col = get_setting_col_index(key)

        if col == None:
            raise Exception('No settings called {}.'.format(key))
        
        row[col] = value

    # Writing to cells.
    cells_to_update = wks.range(row_index, 1, row_index, len(row))
    for i, val in enumerate(row):
        cells_to_update[i].value = val
    
    # Updating cells.
    wks.update_cells(cells_to_update)