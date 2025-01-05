"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""

import os
from typing import Literal

from dotenv import load_dotenv
from postgrest.base_request_builder import APIResponse

from supabase._async.client import create_client, AsyncClient
from bot.config import DEV_MODE
from database.schema import VersionRecord
from database.utils import get_version_string, filter_versions


class DatabaseManager:
    """Singleton class for the supabase client."""

    _is_setup: bool = False
    _async_client: AsyncClient | None = None
    version_cache: dict[str | None, list[VersionRecord]] = {}

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
        cls.version_cache["Java"] = await cls.fetch_versions_list("Java")
        cls.version_cache["Bedrock"] = await cls.fetch_versions_list("Bedrock")

    @classmethod
    async def fetch_versions_list(cls, edition: Literal["Java", "Bedrock"]) -> list[VersionRecord]:
        """Returns a list of versions from the database, sorted from newest to oldest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        await cls.setup()
        query = cls.__new__(cls).table("versions").select("*")
        versions_response: APIResponse[VersionRecord] = (
            await query.eq("edition", edition)
            .order("major_version", desc=True)
            .order("minor_version", desc=True)
            .order("patch_number", desc=True)
            .execute()
        )
        return versions_response.data

    @classmethod
    def get_versions_list(cls, edition: Literal["Java", "Bedrock"]) -> list[VersionRecord]:
        """Returns a list of all minecraft versions"""
        versions = cls.version_cache.get(edition)
        if versions is None:
            raise RuntimeError("DatabaseManager not set up yet. Call await DatabaseManager.setup() first.")
        return versions

    @classmethod
    async def fetch_newest_version(cls, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version from the database. This method is cached."""
        versions = await cls.fetch_versions_list(edition=edition)
        return get_version_string(versions[0], no_edition=True)

    @classmethod
    def get_newest_version(cls, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version, formatted like '1.17.1'."""
        versions = cls.get_versions_list(edition=edition)[0]
        return get_version_string(versions, no_edition=True)


async def main():
    await DatabaseManager.setup()
    spec_string = "1.14 - 1.16.1, 1.17, 1.19+"
    print(await filter_versions(spec_string))

if __name__ == "__main__":
    import asyncio
    import dotenv

    dotenv.load_dotenv()
    asyncio.run(main())
