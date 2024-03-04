import configparser
import os
from supabase import create_client, Client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_KEY')

if not url:
    if os.path.isfile('auth.ini'):
        config = configparser.ConfigParser()
        config.read('auth.ini')
        url = config.get('supabase', 'SUPABASE_URL')
    else:
        raise Exception('Specify supabase url either with an auth.ini or a SUPABASE_URL environment variable.')
if not key:
    if os.path.isfile('auth.ini'):
        config = configparser.ConfigParser()
        config.read('auth.ini')
        key = config.get('supabase', 'SUPABASE_KEY')
    else:
        raise Exception('Specify supabase key either with an auth.ini or a SUPABASE_KEY environment variable.')

class DatabaseManager:
    """Singleton class for the supabase client."""
    _client: Client = None

    def __new__(cls):
        if not cls._client:
            cls._client = create_client(url, key)
        return cls._client
