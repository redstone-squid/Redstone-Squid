import configparser
import os
import asyncio
from pathlib import Path
from supabase_py_async import create_client, AsyncClient
from supabase_py_async.lib.client_options import ClientOptions

all_build_columns = '*, versions(*), build_links(*), build_creators(*), types(*), restrictions(*), doors(*), extenders(*), utilities(*), entrances(*)'

class DatabaseManager:
    """Singleton class for the supabase client."""
    _client: AsyncClient = None


    # This actually works, but some IDE might show a warning
    async def __new__(cls):
        if not cls._client:
            url, key = cls.get_credentials()
            cls._client = await create_client(url, key)
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


async def main():
    from pprint import pprint
    db = await DatabaseManager()
    response = await db.table('builds').select(all_build_columns).eq('id', 30).maybe_single().execute()
    pprint(response.data, sort_dicts=False)

if __name__ == '__main__':
    asyncio.run(main())
