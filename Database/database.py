import configparser
import os
from pprint import pprint
from supabase import create_client, Client

from Database.submission import Submission

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_KEY')

if not url:
    if os.path.isfile('auth.ini'):
        config = configparser.ConfigParser()
        config.read('auth.ini')
        TOKEN = config.get('supabase', 'SUPABASE_URL')
    else:
        raise Exception('Specify discord token either with an auth.ini or a DISCORD_TOKEN environment variable.')
if not key:
    if os.path.isfile('auth.ini'):
        config = configparser.ConfigParser()
        config.read('auth.ini')
        TOKEN = config.get('supabase', 'SUPABASE_KEY')
    else:
        raise Exception('Specify discord token either with an auth.ini or a DISCORD_TOKEN environment variable.')

class DatabaseManager:
    """Singleton class for the supabase client."""
    _client: Client = None

    def __new__(cls):
        if not cls._client:
            cls._client = create_client(url, key)
        return cls._client


if __name__ == '__main__':
    db: Client = create_client(url, key)
    # server_id = 433618741528625152
    server_id = 0
    # setting_name = 'smallest_observerless_channel_id'
    setting_name = 'smallest_channel_id'
    value = 1
    submission_id = 1
    # data = (db.table('messages')
    #         .select('*, submissions(*)')
    #         .eq('server_id', server_id)
    #         .eq('submissions.submission_status', Submission.CONFIRMED)
    #         .lt('last_updated', 'submissions.last_updated')
    #         .execute()
    #         .data)
    data = db.table('submissions').update({'submission_status': Submission.CONFIRMED}, count='exact').eq('submission_id', submission_id).execute()
    pprint(data)
