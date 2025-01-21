"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any, ClassVar, Literal, TYPE_CHECKING, overload
from functools import cache

from dotenv import load_dotenv
from postgrest.base_request_builder import APIResponse

from pydantic import TypeAdapter, ValidationError
from supabase._async.client import AsyncClient
from supabase.lib.client_options import AsyncClientOptions
from bot.config import DEV_MODE
from database.message import MessageManager
from database.schema import DeleteLogVoteSessionRecord, MessageRecord, RestrictionRecord, VersionRecord
from database.server_settings import ServerSettingManager
from database.utils import get_version_string, parse_version_string

if TYPE_CHECKING:
    import discord
    from bot.main import RedstoneSquid


class DatabaseManager(AsyncClient):
    """Singleton class for the supabase client."""

    version_cache: ClassVar[dict[str | None, list[VersionRecord]]] = {}
    _instance: ClassVar[DatabaseManager] | None = None
    bot: RedstoneSquid | None = None

    def __new__(cls, *args, **kwargs) -> DatabaseManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        bot: RedstoneSquid | None = None,
        options: AsyncClientOptions | None = None,
    ):
        """Initializes the DatabaseManager."""
        # Singleton object should only be initialized once
        if self._initialized:
            return
        self._initialized = True

        # This is necessary if the user is not running from app.py.
        if DEV_MODE:
            load_dotenv()
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_KEY")
        if not supabase_url:
            raise RuntimeError("Specify SUPABASE_URL either with a .env file or a SUPABASE_URL environment variable.")
        if not supabase_key:
            raise RuntimeError("Specify SUPABASE_KEY either with a .env file or a SUPABASE_KEY environment variable.")

        super().__init__(supabase_url, supabase_key, options)
        self.bot = bot
        self.server_setting = ServerSettingManager(self)
        self.message = MessageManager(self)

    # TODO: Invalidate cache every, say, 1 day (or make supabase callback whenever the table is updated)
    @cache
    async def fetch_all_restrictions(self) -> list[RestrictionRecord]:
        """Fetches all restrictions from the database."""
        response: APIResponse[RestrictionRecord] = await self.table("restrictions").select("*").execute()
        return response.data

    async def get_or_fetch_versions_list(self, edition: Literal["Java", "Bedrock"]) -> list[VersionRecord]:
        """Returns a list of versions from the database, sorted from oldest to newest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        if versions := self.version_cache.get(edition):
            return versions

        versions_response: APIResponse[VersionRecord] = (
            await self.table("versions")
            .select("*")
            .eq("edition", edition)
            .order("major_version")
            .order("minor_version")
            .order("patch_number")
            .execute()
        )
        self.version_cache[edition] = versions_response.data
        return versions_response.data

    async def get_or_fetch_newest_version(self, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version from the database. This method is cached."""
        versions = await self.get_or_fetch_versions_list(edition=edition)
        return get_version_string(versions[-1])

    async def find_versions_from_spec(self, version_spec: str) -> list[str]:
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

        all_versions = await self.get_or_fetch_versions_list("Java")
        all_version_tuples = [(v["major_version"], v["minor_version"], v["patch_number"]) for v in all_versions]

        # Split the spec by commas: e.g. "1.14 - 1.16.1, 1.17, 1.19+" has 3 parts
        parts = [part.strip() for part in version_spec.split(",")]

        valid_tuples: list[tuple[int, int, int]] = []

        for part in parts:
            # Case 1: range like "1.14 - 1.16.1"
            if "-" in part:
                subparts = [p.strip() for p in part.split("-")]
                if len(subparts) != 2:
                    raise ValueError(f"Invalid version range format in {part}, expected exactly 2 parts, got {len(subparts)}.")
                start_str, end_str = subparts
                start_tuple = parse_version_string(start_str) if start_str.count(".") == 2 else parse_version_string(start_str + ".0")
                # FIXME: for something like 1.14 - 1.16, 1.16 should mean the last patch of 1.16, not 1.16.0
                end_tuple = parse_version_string(end_str) if end_str.count(".") == 2 else parse_version_string(end_str + ".0")

                for v_tuple in all_version_tuples:
                    if start_tuple[1:] <= v_tuple <= end_tuple[1:]:
                        valid_tuples.append(v_tuple)

            # Case 2: trailing plus like "1.19+"
            elif part.endswith("+"):
                base_str = part[:-1].strip()
                # Change "1.19+" to "1.19.0+"
                if base_str.count(".") == 1:
                    base_str += ".0"
                base_tuple = parse_version_string(base_str)

                for v_tuple in all_version_tuples:
                    if v_tuple >= base_tuple[1:]:
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

    @overload
    async def getch(self, record: MessageRecord | DeleteLogVoteSessionRecord) -> discord.Message | None: ...  # pyright: ignore

    async def getch(self, record: Mapping[str, Any]) -> Any:
        """Fetch discord objects from database records."""
        if self.bot is None:
            raise RuntimeError("Bot instance not set.")

        try:
            message_adapter = TypeAdapter(MessageRecord)
            message_adapter.validate_python(record)
            return await self.bot.get_or_fetch_message(record["channel_id"], record["message_id"])
        except ValidationError:
            pass

        try:
            message_adapter = TypeAdapter(DeleteLogVoteSessionRecord)
            message_adapter.validate_python(record)
            return await self.bot.get_or_fetch_message(record["target_channel_id"], record["target_message_id"])
        except ValidationError:
            pass

        raise ValueError("Invalid object to fetch.")


async def main():
    spec_string = "1.14 - 1.16.1, 1.17, 1.19+"
    print(DatabaseManager().find_versions_from_spec(spec_string))
    r = await DatabaseManager().rpc("find_restriction_ids", {"search_terms": ["Seamless", "No Observers"]}).execute()
    print(r.data)


if __name__ == "__main__":
    import asyncio
    import dotenv

    dotenv.load_dotenv()
    asyncio.run(main())
