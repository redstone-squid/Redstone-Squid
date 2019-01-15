import Database.config as config
import Google.interface as Google

# Gets the workbook which contains the Bot's database.
def get_workbook():
    return Google.Connection.get().open(config.WORKBOOK_NAME)

# Gets the worksheet which contains the global variables.
def get_globals_worksheet():
    return get_workbook().get_worksheet(config.GLOBALS_SHEET_INDEX)

# Get the worksheet which contains the server settings.
def get_server_settings_worksheet():
    return get_workbook().get_worksheet(config.SERVER_SETTINGS_SHEET_INDEX)

# Gets the worksheet which contains the submissions from the google form.
def get_form_submissions_worksheet():
    return get_workbook().get_worksheet(config.FORM_SUBMISSIONS_SHEET_INDEX)

# Gets the worksheet which contains the open submissions.
def get_open_submissions_worksheet():
    return get_workbook().get_worksheet(config.OPEN_SUBMISSIONS_SHEET_INDEX)

# Gets the worksheet which contains the confirmed submissions.
def get_confirmed_submissions_worksheet():
    return get_workbook().get_worksheet(config.CONFIRMED_SUBMISSIONS_SHEET_INDEX)

# Gets the worksheet which contains the denied submissions.
def get_denied_submissions_worksheet():
    return get_workbook().get_worksheet(config.DENIED_SUBMISSIONS_SHEET_INDEX)

# Returns a list of possible settings.
def header_list(wks):
    return wks.row_values(1)

# Returns the amount of settings a server can have.
def header_count(wks):
    return len(header_list(wks))

# Gets the column in the database which contains the setting.
def get_col_index(wks, header_name):
    # Makes a list of headers.
    headers = wks.row_values(1)

    # Finding the column which contains the correct setting name.
    try:
        return headers.index(header_name)
    except ValueError:
        return None
    
def get_col_number(wks, header_name):
    index = get_col_index(wks, header_name)
    return None if index == None else index + 1