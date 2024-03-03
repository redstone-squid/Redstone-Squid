from datetime import datetime
from gspread import Worksheet

from Database.database import DatabaseManager
from Database.submission import Submission

def add_submission(submission: Submission) -> None:
    """Adds a submission to the form submissions worksheet."""
    add_submission_raw(submission.to_dict())

def add_submission_raw(submission: dict) -> None:
    """Adds a submission to the form submissions worksheet."""
    db = DatabaseManager()
    db.table('submissions').insert(submission).execute()

def get_open_submissions_raw() -> list[dict]:
    db = DatabaseManager()
    response = db.table('submissions').select('*').eq('submission_status', Submission.PENDING).execute()
    return response.data

def get_open_submissions() -> list[Submission]:
    submissions_dict = get_open_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]
    return submissions

def get_confirmed_submissions_raw() -> list[dict]:
    db = DatabaseManager()
    response = db.table('submissions').select('*').eq('submission_status', Submission.CONFIRMED).execute()
    return response.data

def get_confirmed_submissions() -> list[Submission]:
    submissions_dict = get_confirmed_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]
    return submissions

def get_denied_submissions_raw() -> list[dict]:
    db = DatabaseManager()
    response = db.table('submissions').select('*').eq('submission_status', Submission.DENIED).execute()
    return response.data

def get_denied_submissions() -> list[Submission]:
    submissions_dict = get_denied_submissions_raw()
    submissions = [Submission.from_dict(submission) for submission in submissions_dict]
    return submissions

def get_submission(submission_id: int) -> Submission | None:
    db = DatabaseManager()
    response = db.table('submissions').select('*').eq('submission_id', submission_id).execute()
    return Submission.from_dict(response.data[0]) if response.data else None

def confirm_submission(submission_id: int) -> Submission | None:
    db = DatabaseManager()
    response = db.table('submissions').update({'submission_status': Submission.CONFIRMED}, count='exact').eq('submission_id', submission_id).execute()
    if response.count == 1:
        return Submission.from_dict(response.data[0])
    return None

def deny_submission(submission_id: int) -> Submission | None:
    db = DatabaseManager()
    response = db.table('submissions').update({'submission_status': Submission.DENIED}, count='exact').eq('submission_id', submission_id).execute()
    if response.count == 1:
        return Submission.from_dict(response.data[0])
    return None
