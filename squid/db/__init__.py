"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""

import os
from typing import ClassVar, Literal

from async_lru import alru_cache
from postgrest.base_request_builder import APIResponse

from realtime._async.channel import AsyncRealtimeChannel
from squid.db.message import MessageManager
from squid.db.schema import RestrictionRecord, VersionRecord
from squid.db.server_settings import ServerSettingManager
from squid.db.utils import get_version_string, parse_version_string
from supabase._async.client import AsyncClient
from supabase.lib.client_options import AsyncClientOptions


class DatabaseManager(AsyncClient):
    """Singleton class for the supabase client."""

    _instance: ClassVar[DatabaseManager | None] = None
    _realtime_restriction_channel: ClassVar[AsyncRealtimeChannel | None] = None
    _realtime_version_channel: ClassVar[AsyncRealtimeChannel | None] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> DatabaseManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        options: AsyncClientOptions | None = None,
    ):
        """Initializes the DatabaseManager."""
        supabase_url = supabase_url or os.environ.get("SUPABASE_URL")
        supabase_key = supabase_key or os.environ.get("SUPABASE_KEY")
        if not supabase_url:
            raise RuntimeError(
                "supabase_url not given and no SUPABASE_URL environmental variable found. "
                "Specify SUPABASE_URL either with a .env file or a SUPABASE_URL environment variable."
            )
        if not supabase_key:
            raise RuntimeError(
                "supabase_key not given and no SUPABASE_KEY environmental variable found. "
                "Specify SUPABASE_KEY either with a .env file or a SUPABASE_KEY environment variable."
            )

        super().__init__(supabase_url, supabase_key, options)
        self.server_setting = ServerSettingManager(self)
        self.message = MessageManager(self)

    @alru_cache
    async def fetch_all_restrictions(self) -> list[RestrictionRecord]:
        """Fetches all restrictions from the database."""
        response: APIResponse[RestrictionRecord] = await self.table("restrictions").select("*").execute()

        # Subscribe to realtime updates to invalidate the cache
        if not self._realtime_restriction_channel:
            self._realtime_restriction_channel = (
                await self.channel("restrictions")
                .on_postgres_changes("*", schema="public", table="restrictions", callback=self.handle_record_updated)
                .subscribe()
            )

        return response.data

    def handle_restriction_updated(self, _payload: dict[str, Any]) -> None:
        """Callback for when a restriction is updated."""
        self.fetch_all_restrictions.cache_clear()

    def handle_version_updated(self, _payload: dict[str, Any]) -> None:
        """Callback for when a version is updated."""
        self.fetch_versions_list.cache_clear()

    @alru_cache
    async def fetch_versions_list(self, edition: Literal["Java", "Bedrock"]) -> list[VersionRecord]:
        """Returns a list of versions from the database, sorted from oldest to newest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        versions_response: APIResponse[VersionRecord] = (
            await self.table("versions")
            .select("*")
            .eq("edition", edition)
            .order("major_version")
            .order("minor_version")
            .order("patch_number")
            .execute()
        )

        # Subscribe to realtime updates to invalidate the cache
        if not self._realtime_version_channel:
            self._realtime_version_channel = (
                await self.channel("versions")
                .on_postgres_changes("*", schema="public", table="versions", callback=self.handle_version_updated)
                .subscribe()
            )

        return versions_response.data

    async def fetch_newest_version(self, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version from the database. This method is cached."""
        versions = await self.fetch_versions_list(edition=edition)
        if len(versions) == 0:
            raise RuntimeError(f"No {edition} versions found.")
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

        all_versions = await self.fetch_versions_list(edition)
        all_version_tuples = [(v["major_version"], v["minor_version"], v["patch_number"]) for v in all_versions]

        # Split the spec by commas: e.g. "1.14 - 1.16.1, 1.17, 1.19+" has 3 parts
        parts = [part.strip() for part in version_spec.split(",")]

        valid_tuples: list[tuple[int, int, int]] = []
        v_tuple: tuple[int, int, int]

        for part in parts:
            # Case 1: range like "1.14 - 1.16.1"
            if "-" in part:
                subparts = [p.strip() for p in part.split("-")]
                if len(subparts) != 2:
                    raise ValueError(
                        f"Invalid version range format in {part}, expected exactly 2 parts, got {len(subparts)}."
                    )
                start_str, end_str = subparts
                start_tuple = (
                    parse_version_string(start_str)
                    if start_str.count(".") == 2
                    else parse_version_string(start_str + ".0")
                )

                if end_str.count(".") == 2:
                    end_tuple = parse_version_string(end_str)
                else:
                    # When no patch is specified (e.g. "1.16"), find the highest patch number for that major.minor
                    major, minor = map(int, end_str.split("."))
                    max_patch = 0
                    for v_tuple in all_version_tuples:
                        if v_tuple[0] == major and v_tuple[1] == minor:
                            max_patch = max(max_patch, v_tuple[2])
                    end_tuple = (major, minor, max_patch)

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
                    v_tuple = tuple(map(int, subparts))  # pyright: ignore[reportAssignmentType]
                    if v_tuple in all_version_tuples:
                        valid_tuples.append(v_tuple)
                else:
                    # Optionally, handle malformed inputs or major-only specs
                    pass

        return [f"{edition} {major}.{minor}.{patch}" for major, minor, patch in valid_tuples]


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
