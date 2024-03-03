from datetime import datetime

from Database.database import DatabaseManager
from Database.submission import Submission
import Database.submissions as submissions

def get_messages(server_id: int) -> list[dict[str, str | int]]:
    db = DatabaseManager()
    server_records = db.table('messages').select('*').eq('server_id', server_id).execute().data
    return server_records

def get_message(server_id: int, submission_id: int) -> dict[str, str | int] | None:
    db = DatabaseManager()
    server_record = db.table('messages').select('*').eq('server_id', server_id).eq('submission_id', submission_id).maybe_single().execute().data
    if len(server_record) == 0:
        return None
    return server_record

def add_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    db = DatabaseManager()
    db.table('messages').insert({
        'server_id': server_id,
        'submission_id': submission_id,
        'channel_id': channel_id,
        'message_id': message_id,
        'last_updated': datetime.now().strftime(r'%Y-%m-%d %H:%M:%S')
    }).execute()

def update_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    # Try getting the message
    message = get_message(server_id, submission_id)

    # If message isn't yet tracked, add it.
    if message is None:
        add_message(server_id, submission_id, channel_id, message_id)
        return
    
    # Update the message
    db = DatabaseManager()
    db.table('messages').update({
        'channel_id': channel_id,
        'message_id': message_id,
        'last_updated': datetime.now().strftime(r'%Y-%m-%d %H:%M:%S')
    }).eq('server_id', server_id).eq('submission_id', submission_id).execute()

# def get_outdated_messages(server_id: int) -> list[tuple[dict[str, str | int] | None, Submission]]:
#     """Returns a list of messages that are outdated, and the submission they are associated with.
#
#     Args:
#         server_id: The server id to check for outdated messages.
#
#     Returns:
#         A list of tuples, where the first element is the message and the second is the submission.
#         If the message is None, it means the submission has no message.
#     """
#     # # Getting messages from database
#     # messages = get_messages(server_id)
#     #
#     # # Getting all submissions
#     # confirmed_submissions = submissions.get_confirmed_submissions()
#     #
#     # # Get list of which ones are out of data
#     # outdated = []
#     # for sub in confirmed_submissions:
#     #     message = None
#     #
#     #     # Getting message if it exists
#     #     for mes in messages:
#     #         if mes['submission_id'] == sub.id:
#     #             message = mes
#     #             break
#     #
#     #     # If message doesn't exist
#     #     if message is None:
#     #         outdated.append((message, sub))
#     #         continue
#     #
#     #     # If the message was last edited before the submission's last update
#     #     if datetime.strptime(message['last_updated'], r'%Y-%m-%d %H:%M:%S') < sub.last_updated:
#     #         outdated.append((message, sub))
#     #         continue
#     #
#     # return outdated
#
#     db = DatabaseManager()
#     # Messages that have been updated since the last submission message update
#     outdated_messages = (db.table('messages')
#                          .select('*', 'submissions(*)')
#                          .eq('server_id', server_id)
#                          .eq('submissions.submission_status', Submission.CONFIRMED)
#                          .lt('last_updated', 'submissions.last_updated')
#                          .execute()
#                          .data)
#
#     # Select all confirmed submission ids, then filter out the ones that have messages
#     server_messages = (db.table('messages')
#                        .select('submission_id')
#                        .eq('server_id', server_id)
#                        .execute()
#                        .data)
#
#     # Messages that have not been posted on the server
#     nonexistent_messages = (db.table('submissions')
#                             .select('*', 'messages(*)')
#                             .eq('messages(server_id)', server_id)
#                             .not_.in_('submission_id', [message['submission_id'] for message in server_messages])
#                             .execute()
#                             .data)
#
#     Not Implemented
#
# def get_outdated_message(server_id: int, submission_id: int) -> tuple[dict[str, str | int] | None, Submission] | None:
#     fucking implement this
