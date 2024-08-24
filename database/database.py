import os
from dotenv import load_dotenv

from supabase_py_async import create_client, AsyncClient
from bot.config import DEV_MODE


class DatabaseManager:
    """Singleton class for the supabase client."""

    _is_setup: bool = False
    _async_client: AsyncClient | None = None

    def __new__(cls) -> AsyncClient:
        if not cls._is_setup:
            raise RuntimeError("DatabaseManager not set up yet. Call await DatabaseManager.setup() first.")
        assert cls._async_client is not None
        return cls._async_client

    @classmethod
    async def setup(cls) -> None:
        """Connects to the Supabase database.

        This method should be called before using the DatabaseManager instance. This method exists because it is hard to use async code in __init__ or __new__."""
        if cls._is_setup:
            return

        # This is necessary only if you are not running from app.py.
        if DEV_MODE:
            load_dotenv()

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url:
            raise RuntimeError("Specify SUPABASE_URL either with a .env file or a SUPABASE_URL environment variable.")
        if not key:
            raise RuntimeError("Specify SUPABASE_KEY either with an auth.ini or a SUPABASE_KEY environment variable.")
        cls._async_client = await create_client(url, key)
        cls._is_setup = True

        # TODO: Create the tables if they don't exist (helpful for making new instances of the bot)


async def main():
    await DatabaseManager.setup()
    print(await DatabaseManager().from_("versions").select("*").execute())


if __name__ == "__main__":
    import asyncio
    import dotenv
    dotenv.load_dotenv()
    asyncio.run(main())
