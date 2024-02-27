from datetime import datetime

import Database.main as DB
from Database.submission import Submission
import Database.submissions as submissions

def get_messages(server_id: int) -> list[dict[str, str | int]]:
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Getting all records for server
    all_records = wks.get_all_records()
    server_records = []
    for index, record in enumerate(all_records):
        if int(record['Server ID']) == server_id:
            record['Row Number'] = index + 2
            server_records.append(record)
    
    return server_records

def get_message(server_id: int, submission_id: int) -> dict[str, str | int] | None:
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # TODO: get_all_records() returns a list of [{'col1_name': 'col1_name'}, ...] if the sheet only has one row. This is a bug.
    # Getting the record with requested server and submission id
    all_records = wks.get_all_records()

    for index, record in enumerate(all_records):
        try:
            if int(record['Server ID']) == server_id and int(record['Submission ID']) == submission_id:
                record['Row Number'] = index + 2
                return record
        except Exception as e:
            print(f"Error: {e}")
            print(f"Record: {record}")
            raise e
    
    return None

def add_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Generating row
    # The ids are converted to strings to avoid gspread rounding off large numbers
    row_values = [None] * wks.col_count
    row_values[DB.get_col_index(wks, 'Server ID')] = str(server_id)
    row_values[DB.get_col_index(wks, 'Submission ID')] = str(submission_id)
    row_values[DB.get_col_index(wks, 'Channel ID')] = str(channel_id)
    row_values[DB.get_col_index(wks, 'Message ID')] = str(message_id)
    row_values[DB.get_col_index(wks, 'Last Updated')] = datetime.now().strftime(r'%d-%m-%Y %H:%M:%S')

    # Appending row to wks
    wks.append_row(row_values)

def update_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Getting message
    message = get_message(server_id, submission_id)
    
    # If message isn't yet tracked, add it.
    if message is None:
        add_message(server_id, submission_id, channel_id, message_id)
        return
    
    # Generating new row information
    row_number = message['Row Number']
    length = wks.col_count

    row_values = wks.row_values(row_number)
    cha_col = DB.get_col_index(wks, 'Channel ID')
    msg_col = DB.get_col_index(wks, 'Message ID')
    time_col = DB.get_col_index(wks, 'Last Updated')

    row_values[msg_col] = message_id
    row_values[cha_col] = channel_id
    row_values[time_col] = datetime.now().strftime(r'%d-%m-%Y %H:%M:%S')

    # Writing to cells
    cells_to_update = wks.range(row_number, 1, row_number, length)
    for i, val in enumerate(row_values):
        cells_to_update[i].value = str(val)
    
    # Updating cells
    wks.update_cells(cells_to_update)

def get_outdated_messages(server_id: int) -> list[tuple[dict[str, str | int] | None, Submission]]:
    # Getting messages from database
    messages = get_messages(server_id)

    # Getting all submissions
    confirmed_submissions = submissions.get_confirmed_submissions()

    # Get list of which ones are out of data
    outdated = []
    for sub in confirmed_submissions:
        message = None

        # Getting message if it exists
        for mes in messages:
            if int(mes['Submission ID']) == sub.id:
                message = mes
                break

        # If message doesn't exist
        if message is None:
            outdated.append((message, sub))
            continue

        # If the message was last edited before the submission's last update
        if datetime.strptime(message['Last Updated'], r'%d-%m-%Y %H:%M:%S') < sub.last_updated:
            outdated.append((message, sub))
            continue
    
    return outdated
