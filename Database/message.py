"""Some functions related to the message table, which stores message ids."""
from datetime import datetime

from Database.database import DatabaseManager


# TODO: Find better names for these functions, the "message" is not really a discord message, but a record in the database.
def get_messages(server_id: int) -> list[dict[str, int]]:
    db = DatabaseManager()
    server_records = db.table('messages').select('*').eq('server_id', server_id).execute().data
    return server_records

def get_message(server_id: int, submission_id: int) -> dict[str, int] | None:
    db = DatabaseManager()
    # supabase hate .maybe_single() and throws a 406 error if no records are found
    server_record = db.table('messages').select('*').eq('server_id', server_id).eq('submission_id', submission_id).execute().data
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


def delete_message(server_id: int, submission_id: int) -> list[int]:
    """Remove a message from the database.

    Args:
        server_id: The server id of the message to delete.
        submission_id: The submission id of the message to delete.

    Raises:
        ValueError: If the message is not found.

    Returns:
        A list of message ids that were deleted.
    """
    db = DatabaseManager()
    response = db.table('messages').select('message_id').eq('server_id', server_id).eq('submission_id', submission_id).execute()
    if response.count == 0:
        raise ValueError("No messages found in this server with the given submission id.")
    message_ids = [response.data[i]['message_id'] for i in range(response.count)]
    db.table('messages').delete().in_('message_id', message_ids).execute()
    return message_ids

def get_outdated_messages(server_id: int) -> list[dict[str, int]] | None:
    """Returns a list of messages that are outdated. Usually `get_submissions` is called in combination with this function.

    Args:
        server_id: The server id to check for outdated messages.

    Returns:
        A list of messages.
    """
    db = DatabaseManager()
    # Messages that have been updated since the last submission message update.
    server_outdated_messages = db.rpc('get_outdated_messages', {'server_id_input': server_id}).execute().data
    if len(server_outdated_messages) == 0:
        return None
    return server_outdated_messages


def get_outdated_message(server_id: int, submission_id: int) -> dict[str, int] | None:
    """Returns a message that is outdated. Usually `get_submission` is called in combination with this function.

    Args:
        server_id: The server id to check for outdated messages.
        submission_id: The submission id to check for outdated message.

    Returns:
        A dictionary containing all the information about the outdated message.
    """
    db = DatabaseManager()
    # Messages that have been updated since the last submission message update.
    server_outdated_messages = db.rpc('get_outdated_messages', {'server_id_input': server_id}).eq('submission_id', submission_id).execute().data
    if len(server_outdated_messages) == 0:
        return None
    return server_outdated_messages[0]


def get_submission_id_by_message(message_id: int) -> int | None:
    db = DatabaseManager()
    response = db.table('messages').select('submission_id', count='exact').eq('message_id', message_id).maybe_single().execute()
    if response.count == 0:
        return None
    return response.data['submission_id']


if __name__ == '__main__':
    # print(get_outdated_message(433618741528625152, 30))
    # print(get_outdated_messages(433618741528625152))
    print(get_submission_id_by_message(536004554743873556))
