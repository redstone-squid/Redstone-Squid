from datetime import datetime

import Database.main as DB
from Database.submission import Submission as Submission_Class
import Database.global_vars as global_vars
import Database.config as config
import Google.interface as Google

import Database.submission as submission

def get_messages(server_id):
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Getting all records for server
    all_records = wks.get_all_records()
    server_records = []
    for index, record in enumerate(all_records):
        if int(record['Server ID']) == int(server_id):
            record['Row Number'] = index + 2
            server_records.append(record)
    
    return server_records

def get_message(server_id, submission_id):
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Getting the record with requested server and submission id
    all_records = wks.get_all_records()

    for record in all_records:
        if int(record['Server ID']) == int(server_id) and int(record['Submission ID']) == int(submission_id):
            return record
    
    return None

def add_message(server_id, submission_id, message_id):
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Generating row
    row_values = [None] * wks.col_count
    row_values[DB.get_col_index(wks, 'Server ID')] = server_id
    row_values[DB.get_col_index(wks, 'Submission ID')] = submission_id
    row_values[DB.get_col_index(wks, 'Message ID')] = message_id
    row_values[DB.get_col_index(wks, 'Last Updated')] = str(datetime.now())

    # Appending row to wks
    wks.append_row(row_values)

def update_message(server_id, submission_id, message_id):
    # Getting worksheet
    wks = DB.get_message_worksheet()

    # Getting message
    message = get_message(server_id, submission_id)
    
    # If message isn't yet tracked, add it.
    if message == None:
        add_message(server_id, submission_id, message_id)
        return
    
    # Generating new row information
    row_number = message['Row Number']
    length = wks.col_count

    row_values = wks.row_values(row_number)
    msg_col = DB.get_col_index(wks, 'Message ID')
    time_col = DB.get_col_index(wks, 'Last Updated')

    row_values[msg_col] = message_id
    row_values[time_col] = str(datetime.now())

    # Writing to cells
    cells_to_update = wks.range(row_number, 1, row_number, length)
    for i, val in enumerate(row_values):
        cells_to_update[i].value = val
    
    # Updating cells
    wks.update_cells(cells_to_update)