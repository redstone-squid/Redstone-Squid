import Database.main as DB
import Database.config as config
import Google.interface as Google

# Submission ID is the primary key for the submissions in the whole database.
# Next ID is the next available submission ID.
def get_next_id():
    wks = DB.get_globals_worksheet()
    return int(wks.get_all_records()[0]['Next ID'])

def update_next_id(new_next_id):
    wks = DB.get_globals_worksheet()
    col_num = DB.get_col_number(wks, 'Next ID')
    wks.update_cell(2, col_num, new_next_id)

def add_to_next_id(count):
    wks = DB.get_globals_worksheet()
    next_id = int(wks.get_all_records()[0]['Next ID']) + count
    col_num = DB.get_col_number(wks, 'Next ID')
    wks.update_cell(2, col_num, next_id)