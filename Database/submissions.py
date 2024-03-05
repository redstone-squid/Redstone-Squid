from Database.database import DatabaseManager
from Discord.submission.submission import Submission

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
    response = db.table('submissions').select('*').eq('submission_id', submission_id).maybe_single().execute()
    return Submission.from_dict(response.data) if response.data else None

def get_submissions(submission_ids: list[int]) -> list[Submission | None]:
    if len(submission_ids) == 0:
        return []

    db = DatabaseManager()
    response = db.table('submissions').select('*').in_('submission_id', submission_ids).execute()

    # Insert None for missing submissions
    submissions: list[Submission | None] = [None] * len(submission_ids)
    for submission in response.data:
        submissions[submission_ids.index(submission['submission_id'])] = Submission.from_dict(submission)
    return submissions

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

def get_unsent_submissions(server_id: int) -> list[Submission] | None:
    """Get all the submissions that have not been posted on the server"""
    db = DatabaseManager()

    # Submissions that have not been posted on the server
    server_unsent_submissions = db.rpc('get_unsent_submissions', {'server_id_input': server_id}).execute().data
    return [Submission.from_dict(unsent_sub) for unsent_sub in server_unsent_submissions]


if __name__ == '__main__':
    print(get_submissions([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]))
