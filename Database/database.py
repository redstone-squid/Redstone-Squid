import configparser
import os
from pathlib import Path
from supabase import create_client, Client

class DatabaseManager:
    """Singleton class for the supabase client."""
    _client: Client = None

    def __new__(cls):
        if not cls._client:
            url, key = cls.get_credentials()
            cls._client = create_client(url, key)
        return cls._client

    @staticmethod
    def get_credentials() -> tuple[str, str]:
        """Get the Supabase credentials from the environment variables or the auth.ini file."""
        # Try to get the credentials from the environment variables
        url = os.environ.get('SUPABASE_URL')
        key = os.environ.get('SUPABASE_KEY')

        config_file = Path(__file__).parent.parent / 'auth.ini'
        if not url or not key:
            # Try to get the credentials from the auth.ini file
            config = configparser.ConfigParser()

            if os.path.isfile(config_file):
                config.read(config_file)
                url = url or config.get('supabase', 'SUPABASE_URL')
                key = key or config.get('supabase', 'SUPABASE_KEY')

        if not url:
            raise Exception(f'Specify SUPABASE_URL either with an auth.ini or an environment variable.')
        if not key:
            raise Exception(f'Specify SUPABASE_KEY either with an auth.ini or an environment variable.')

        return url, key


if __name__ == '__main__':
    db = DatabaseManager()
    response = db.table('submissions').select('*').eq('submission_id', 1).maybe_single().execute()
    print(response)
