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
from database.utils import get_version_string


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
        """Returns a list of versions from the database, sorted from oldest to newest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        await cls.setup()
        query = cls.__new__(cls).table("versions").select("*")
        versions_response: APIResponse[VersionRecord] = (
            await query.eq("edition", edition)
            .order("major_version")
            .order("minor_version")
            .order("patch_number")
            .execute()
        )
        return versions_response.data

    @classmethod
    def get_versions_list(cls, edition: Literal["Java", "Bedrock"]) -> list[VersionRecord]:
        """Returns a list of all minecraft versions, sorted from oldest to newest"""
        versions = cls.version_cache.get(edition)
        if versions is None:
            raise RuntimeError("DatabaseManager not set up yet. Call await DatabaseManager.setup() first.")
        return versions

    @classmethod
    async def fetch_newest_version(cls, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version from the database. This method is cached."""
        versions = await cls.fetch_versions_list(edition=edition)
        return get_version_string(versions[-1])

    @classmethod
    def get_newest_version(cls, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version, formatted like '1.17.1'."""
        versions = cls.get_versions_list(edition=edition)
        return get_version_string(versions[-1])

    @classmethod
    def find_versions_from_spec(cls, version_spec: str) -> list[str]:
        """Return all versions that match the version specification."""

        # See if the spec specifies no edition (default to Java), one edition, or both
        bedrock = version_spec.find("Bedrock") != -1
        java = version_spec.find("Java") != -1
        if not bedrock and not java:
            edition = "Java"  # Default to Java if no edition specified
        elif bedrock and not java:
            edition = "Bedrock"
        elif not bedrock and java:
            edition = "Java"
        else:
            raise NotImplementedError("Cannot specify both Java and Bedrock in the version spec.")

        version_spec = version_spec.replace("Java", "").replace("Bedrock", "").strip()

        def parse_version(version_str: str):
            major, minor, patch = version_str.split(".")
            return int(major), int(minor), int(patch)

        all_versions = cls.get_versions_list("Java")
        # Convert each version in all_versions into a tuple for easy comparison
        all_version_tuples = [(v["major_version"], v["minor_version"], v["patch_number"]) for v in all_versions]

        # Split the spec by commas: e.g. "1.14 - 1.16.1, 1.17, 1.19+"
        parts = [part.strip() for part in version_spec.split(",")]

        valid_tuples: list[tuple[int, int, int]] = []

        for part in parts:
            # Case 1: range like "1.14 - 1.16.1"
            if "-" in part:
                start_str, end_str = [p.strip() for p in part.split("-")]
                start_tuple = parse_version(start_str) if start_str.count(".") == 2 else parse_version(start_str + ".0")
                end_tuple = parse_version(end_str) if end_str.count(".") == 2 else parse_version(end_str + ".0")

                for v_tuple in all_version_tuples:
                    if start_tuple <= v_tuple <= end_tuple:
                        valid_tuples.append(v_tuple)

            # Case 2: trailing plus like "1.19+"
            elif part.endswith("+"):
                base_str = part[:-1].strip()
                # If user just wrote "1.19+", assume "1.19.0"
                if base_str.count(".") == 1:
                    base_str += ".0"
                base_tuple = parse_version(base_str)

                for v_tuple in all_version_tuples:
                    if v_tuple >= base_tuple:
                        valid_tuples.append(v_tuple)

            # Case 3: exact version or prefix, e.g. "1.17" or "1.17.1"
            else:
                subparts = part.split(".")
                # If only major.minor specified (like "1.17"), match all "1.17.x"
                if len(subparts) == 2:
                    major, minor = map(int, subparts)
                    for v_tuple in all_version_tuples:
                        if v_tuple[0] == major and v_tuple[1] == minor:
                            valid_tuples.append(v_tuple)
                # If a full version specified (like "1.17.1"), match exactly that version
                elif len(subparts) == 3:
                    v_tuple = tuple(map(int, subparts))
                    if v_tuple in all_version_tuples:
                        valid_tuples.append(v_tuple)
                else:
                    # Optionally, handle malformed inputs or major-only specs
                    pass

        return [f"{edition} {major}.{minor}.{patch}" for major, minor, patch in valid_tuples]


async def main():
    await DatabaseManager.setup()
    spec_string = "1.14 - 1.16.1, 1.17, 1.19+"
    print(DatabaseManager.find_versions_from_spec(spec_string))


if __name__ == "__main__":
    import asyncio
    import dotenv

    dotenv.load_dotenv()
    asyncio.run(main())
