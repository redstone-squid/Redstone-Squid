"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""
import os
from typing import Literal

from async_lru import alru_cache
from dotenv import load_dotenv
from postgrest import APIResponse

from supabase._async.client import create_client, AsyncClient
from bot.config import DEV_MODE
from database.schema import VersionRecord
from database.utils import get_version_string


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

    @classmethod
    @alru_cache(maxsize=3)
    async def get_versions_list(cls, *, edition: Literal["Java", "Bedrock"] | None = None) -> list[VersionRecord]:
        """Returns a list of versions from the database, sorted from oldest to newest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        await cls.setup()
        query = cls().table("versions").select("*")
        if edition:
            query = query.eq("edition", edition)
        versions_response: APIResponse[VersionRecord] = (
            await query.order("edition")
            .order("major_version", desc=True)
            .order("minor_version", desc=True)
            .order("patch_number", desc=True)
            .execute()
        )
        return versions_response.data

    @classmethod
    @alru_cache(maxsize=2)
    async def get_newest_version(cls, *, edition: Literal["Java", "Bedrock"]) -> VersionRecord:
        """Returns the newest version from the database. This method is cached."""
        versions = await cls.get_versions_list(edition=edition)
        return versions[-1]


async def main():
    await DatabaseManager.setup()
    print(await DatabaseManager.get_versions_list(edition="Java"))


if __name__ == "__main__":
    import asyncio
    import dotenv

    dotenv.load_dotenv()
    asyncio.run(main())
