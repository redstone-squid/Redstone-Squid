from datetime import datetime

from Database.database import DatabaseManager


def get_messages(server_id: int) -> list[dict[str, int]]:
    db = DatabaseManager()
    server_records = db.table('messages').select('*').eq('server_id', server_id).execute().data
    return server_records

def get_message(server_id: int, submission_id: int) -> dict[str, int] | None:
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
    return server_outdated_messages


if __name__ == '__main__':
    print(get_outdated_message(433618741528625152, 30))
    # print(get_outdated_messages(433618741528625152))
