"""Some functions related to the message table, which stores message ids."""
from common import get_current_utc
from Database.database import DatabaseManager

# FIXME: (server_id, build_id) is not guaranteed to be a superkey, but it is assumed to be unique.
# TODO: Find better names for these functions, the "message" is not really a discord message, but a record in the database.
async def get_messages(server_id: int) -> list[dict[str, int]]:
    db = await DatabaseManager()
    server_records = await db.table('messages').select('*').eq('server_id', server_id).execute().data
    return server_records

async def get_message(server_id: int, submission_id: int) -> dict[str, int] | None:
    db = await DatabaseManager()
    # supabase hate .maybe_single() and throws a 406 error if no records are found
    server_record = await db.table('messages').select('*').eq('server_id', server_id).eq('build_id', submission_id).execute().data
    if len(server_record) == 0:
        return None
    return server_record

async def add_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    db = await DatabaseManager()
    await db.table('messages').insert({
        'server_id': server_id,
        'build_id': submission_id,
        'channel_id': channel_id,
        'message_id': message_id,
        'last_updated': get_current_utc()
    }).execute()

async def update_message(server_id: int, submission_id: int, channel_id: int, message_id: int) -> None:
    # Try getting the message
    message = await get_message(server_id, submission_id)

    # If message isn't yet tracked, add it.
    if message is None:
        await add_message(server_id, submission_id, channel_id, message_id)
        return
    
    # Update the message
    db = await DatabaseManager()
    await db.table('messages').update({
        'channel_id': channel_id,
        'message_id': message_id,
        'last_updated': get_current_utc()
    }).eq('server_id', server_id).eq('build_id', submission_id).execute()


async def delete_message(server_id: int, build_id: int) -> list[int]:
    """Remove a message from the database.

    Args:
        server_id: The server id of the message to delete.
        build_id: The build id of the message to delete.

    Raises:
        ValueError: If the message is not found.

    Returns:
        A list of message ids that were deleted.
    """
    db = await DatabaseManager()
    response = await db.table('messages').select('message_id', count='exact').eq('server_id', server_id).eq('build_id', build_id).execute()
    if response.count == 0:
        raise ValueError("No messages found in this server with the given submission id.")
    message_ids = [response.data[i]['message_id'] for i in range(response.count)]
    await db.table('messages').delete().in_('message_id', message_ids).execute()
    return message_ids

async def get_outdated_messages(server_id: int) -> list[dict[str, int]] | None:
    """Returns a list of messages that are outdated. Usually `get_submissions` is called in combination with this function.

    Args:
        server_id: The server id to check for outdated messages.

    Returns:
        A list of messages.
    """
    db = await DatabaseManager()
    # Messages that have been updated since the last submission message update.
    server_outdated_messages = await db.rpc('get_outdated_messages', {'server_id_input': server_id}).execute().data
    if len(server_outdated_messages) == 0:
        return None
    return server_outdated_messages


async def get_outdated_message(server_id: int, build_id: int) -> dict[str, int] | None:
    """Returns a message that is outdated. Usually `get_submission` is called in combination with this function.

    Args:
        server_id: The server id to check for outdated messages.
        build_id: The build id to check for outdated message.

    Returns:
        A dictionary containing all the information about the outdated message.
    """
    db = await DatabaseManager()
    # Messages that have been updated since the last submission message update.
    server_outdated_messages = await db.rpc('get_outdated_messages', {'server_id_input': server_id}).eq('build_id', build_id).execute().data
    if len(server_outdated_messages) == 0:
        return None
    return server_outdated_messages[0]


async def get_build_id_by_message(message_id: int) -> int | None:
    """
    Get the build id by the message id.

    Args:
        message_id: The message id to get the build id from.

    Returns:
        The build id of the message.
    """
    db = await DatabaseManager()
    response = await db.table('messages').select('build_id', count='exact').eq('message_id', message_id).maybe_single().execute()
    if response.count == 0:
        return None
    return response.data['build_id']


if __name__ == '__main__':
    # print(get_outdated_message(433618741528625152, 30))
    # print(get_outdated_messages(433618741528625152))
    print(get_build_id_by_message(536004554743873556))
