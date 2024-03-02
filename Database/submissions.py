from datetime import datetime
from gspread import Worksheet

import Database.main as DB
from Database.submission import Submission
import Database.global_vars as global_vars
import Database.submission as submission

# Moves all records from the form_submissions worksheet to the open submissions worksheet.
def open_form_submissions():
    # Getting worksheets
    form_wks = DB.get_form_submissions_worksheet()
    open_wks = DB.get_open_submissions_worksheet()

    # Getting submissions from form submissions worksheet
    submissions = form_wks.get_all_values()
    if len(submissions) == 1:
        return  # If there are no submissions, return
    submissions = submissions[1:]
    
    # Getting range of cells that need updating
    submission_count = len(submissions)
    
    first_row = open_wks.row_count + 1
    last_row = open_wks.row_count + submission_count
    first_col = 3
    last_col = open_wks.col_count

    # Adding Rows
    open_wks.add_rows(submission_count)

    # Updating cells
    cells_to_update = open_wks.range(first_row, first_col, last_row, last_col)
    index = 0
    for submission in submissions:
        for element in submission:
            cells_to_update[index].value = element
            index += 1

    open_wks.update_cells(cells_to_update)

    # Adding IDs and Last update times to submissions
    next_id = global_vars.get_next_id()

    cells_to_update = open_wks.range(first_row, 1, last_row, 2)

    for i in range(len(cells_to_update) // 2):
        cells_to_update[i * 2].value = i + next_id
        cells_to_update[i * 2 + 1].value = datetime.now().strftime('%d-%m-%Y %H:%M:%S')

    global_vars.add_to_next_id(submission_count)
    open_wks.update_cells(cells_to_update)

    # Removing Submissions from form worksheet
    for _ in range(submission_count):
        form_wks.delete_row(2)

def add_submission(submission: Submission) -> None:
    """Adds a submission to the form submissions worksheet."""
    add_submission_raw(submission.to_dict())

def add_submission_raw(submission: dict) -> None:
    """Adds a submission to the form submissions worksheet."""
    wks = DB.get_open_submissions_worksheet()
    headers = wks.row_values(1)
    put_values = [None] * len(headers)
    for key, value in submission.items():
        put_values[headers.index(key)] = value
    wks.append_row(put_values)


# Returns all submissions that are in the open submissions worksheet.
def get_open_submissions_raw() -> list[dict]:
    wks = DB.get_open_submissions_worksheet()
    return wks.get_all_records()

def get_open_submissions() -> list[Submission]:
    submissions_dict = get_open_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]

    return submissions

def get_open_submission(submission_id: int) -> Submission | None:
    return get_submission(submission_id, DB.get_open_submissions_worksheet())

# Returns all submissions that are in the confirmed submissions worksheet.
def get_confirmed_submissions_raw() -> list[dict]:
    wks = DB.get_confirmed_submissions_worksheet()
    return wks.get_all_records()

def get_confirmed_submissions() -> list[Submission]:
    submissions_dict = get_confirmed_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]
    return submissions

def get_confirmed_submission(submission_id) -> Submission | None:
    return get_submission(submission_id, DB.get_confirmed_submissions_worksheet())

# Returns all submissions that are in the denied submissions worksheet.
def get_denied_submissions_raw() -> list[dict]:
    wks = DB.get_denied_submissions_worksheet()
    return wks.get_all_records()

def get_denied_submissions() -> list[Submission]:
    submissions_dict = get_denied_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]
    return submissions

def get_denied_submission(submission_id: int) -> Submission | None:
    return get_submission(submission_id, DB.get_denied_submissions_worksheet())

# Returns a Submission object parsed from wks with the submission_id
def get_submission(submission_id: int, wks: Worksheet) -> Submission | None:
    submissions = wks.get_all_records()
    for sub in submissions:
        if int(sub['Submission ID']) == submission_id:
            return submission.Submission.from_dict(sub)
    return None

# Moves submission with submission_id from source_wks to destination_wks
# Returns if the submission exists in source_wks
def move_submission(submission_id: int, source_wks: Worksheet, destination_wks: Worksheet) -> bool:
    # Gets all submission from source
    submissions = source_wks.get_all_records()

    # Finds the row number of the submission
    row_number = None
    for index, sub in enumerate(submissions):
        if int(sub['Submission ID']) == submission_id:
            row_number = index + 2
            break

    # Returns False if no such submission exist
    if row_number is None:
        return False

    # Appends the submission to the destination worksheet
    destination_wks.append_row(source_wks.row_values(row_number))
    # Removes the submission from the source worksheet
    source_wks.delete_rows(row_number)

    return True

# Confirm submission
def confirm_submission(submission_id: int) -> bool:
    return move_submission(submission_id, DB.get_open_submissions_worksheet(), DB.get_confirmed_submissions_worksheet())

# Deny submission
def deny_submission(submission_id: int) -> bool:
    return move_submission(submission_id, DB.get_open_submissions_worksheet(), DB.get_denied_submissions_worksheet())
