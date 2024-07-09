import os
from pathlib import Path
from dotenv import load_dotenv
import git

from supabase_py_async import create_client, AsyncClient
from bot.config import DEV_MODE

all_build_columns = "*, versions(*), build_links(*), build_creators(*), types(*), restrictions(*), doors(*), extenders(*), utilities(*), entrances(*)"


class DatabaseManager:
    """Singleton class for the supabase client."""

    _is_setup: bool = False
    _client: AsyncClient | None = None

    def __new__(cls) -> AsyncClient:
        if not cls._is_setup:
            raise RuntimeError("DatabaseManager not set up yet. Call await DatabaseManager.setup() first.")
        return cls._client  # pyright: ignore [reportReturnType]

    @classmethod
    async def setup(cls) -> None:
        """Connects to the Supabase database.

        This method should be called before using the DatabaseManager instance. This method exists because it is hard to use async code in __init__ or __new__."""
        if cls._is_setup:
            return

        # Load the environment variables from the .env file, which is located in the root of the git repository.
        # This is necessary only if you are not running from app.py.
        if DEV_MODE:
            git_repo = git.Repo(Path(__file__), search_parent_directories=True)
            load_dotenv(git_repo.working_dir + "/.env")  # type: ignore

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url:
            raise RuntimeError("Specify SUPABASE_URL either with a .env file or a SUPABASE_URL environment variable.")
        if not key:
            raise RuntimeError("Specify SUPABASE_KEY either with an auth.ini or a SUPABASE_KEY environment variable.")
        cls._client = await create_client(url, key)
        cls._is_setup = True

        # TODO: Create the tables if they don't exist (helpful for making new instances of the bot)
