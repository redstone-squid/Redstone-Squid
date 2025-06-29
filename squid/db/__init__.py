"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""

import os
from typing import Any, ClassVar, Literal

from sqlalchemy import create_engine, make_url, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from squid.db.build_tags import BuildTagsManager
from squid.db.inspect_db import is_sane_database
from squid.db.message import MessageService
from squid.db.repos.message_repository import MessageRepository
from squid.db.schema import Version
from squid.db.server_settings import ServerSettingManager
from squid.db.user import UserManager
from squid.utils import get_version_string, parse_version_string
from supabase._async.client import AsyncClient
from supabase.lib.client_options import AsyncClientOptions


class DatabaseManager(AsyncClient):
    """Singleton class for the supabase client."""

    version_cache: ClassVar[dict[str | None, list[Version]]] = {}
    _instance: ClassVar["DatabaseManager | None"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        options: AsyncClientOptions | None = None,
        database_url: str | None = None,
        *,
        debug: bool = False,
    ):
        """Initializes the DatabaseManager."""
        if self._initialized:
            return
        self._initialized = True

        supabase_url = supabase_url or os.environ.get("SUPABASE_URL")
        supabase_key = supabase_key or os.environ.get("SUPABASE_KEY")
        database_url = database_url or os.environ.get("DATABASE_URL")
        driver_sync = os.environ.get("DB_DRIVER_SYNC")
        driver_async = os.environ.get("DB_DRIVER_ASYNC")

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
        if not database_url:
            raise RuntimeError(
                "database_url not given and no DATABASE_URL environmental variable found. "
                "Specify DATABASE_URL either with a .env file or a DATABASE_URL environment variable."
            )
        if not driver_sync:
            raise RuntimeError("No DB_DRIVER_SYNC environment variable found.")
        if not driver_async:
            raise RuntimeError("No DB_DRIVER_ASYNC environment variable found.")

        # Initialize Supabase client
        super().__init__(supabase_url, supabase_key, options)

        # Initialize SQLAlchemy engine and session maker
        base = make_url(database_url)
        self.async_engine = create_async_engine(base.set(drivername=f"{base.drivername}+{driver_async}"), echo=debug)
        self.async_session = async_sessionmaker(self.async_engine, expire_on_commit=False)
        self.sync_engine = create_engine(base.set(drivername=f"{base.drivername}+{driver_sync}"), echo=debug)
        self.sync_session = sessionmaker(self.sync_engine, expire_on_commit=False)

        # Initialize repositories and services
        self.message_repo = MessageRepository(self.async_session)
        self.message = MessageService(self.message_repo)

        # Initialize managers
        self.server_setting = ServerSettingManager(self.async_session)
        self.user = UserManager(self.async_session)
        self.build_tags = BuildTagsManager(self.async_session)

    def validate_database_consistency(self, base_cls: type[DeclarativeBase]) -> None:
        """Validates that the database schema is consistent with the expected schema."""
        if not is_sane_database(base_cls, self.sync_engine):
            raise RuntimeError("The database schema is not consistent with the expected schema.")

    async def get_or_fetch_versions_list(self, edition: Literal["Java", "Bedrock"]) -> list[Version]:
        """Returns a list of versions from the database, sorted from oldest to newest.

        If edition is specified, only versions from that edition are returned. This method is cached."""
        if versions := self.version_cache.get(edition):
            return versions

        async with self.async_session() as session:
            stmt = (
                select(Version)
                .where(Version.edition == edition)
                .order_by(
                    Version.major_version,
                    Version.minor_version,
                    Version.patch_number,
                )
            )
            result = await session.execute(stmt)
            version_records = list(result.scalars().all())

        self.version_cache[edition] = version_records
        return version_records

    async def get_or_fetch_newest_version(self, *, edition: Literal["Java", "Bedrock"]) -> str:
        """Returns the newest version from the database. This method is cached."""
        versions = await self.get_or_fetch_versions_list(edition=edition)
        if len(versions) == 0:
            raise RuntimeError(f"No {edition} versions found.")
        return get_version_string(versions[-1])

    async def find_versions_from_spec(self, version_spec: str) -> list[str]:
        """Return all versions that match the version specification."""

        # See if the spec specifies no edition (default to Java), one edition, or both
        bedrock = version_spec.find("Bedrock") != -1
        java = version_spec.find("Java") != -1
        edition: Literal["Java", "Bedrock"]
        if not bedrock and not java:
            edition = "Java"  # Default to Java if no edition specified
        elif bedrock and not java:
            edition = "Bedrock"
        elif not bedrock and java:
            edition = "Java"
        else:
            raise NotImplementedError("Cannot specify both Java and Bedrock in the version spec.")

        version_spec = version_spec.replace("Java", "").replace("Bedrock", "").strip()

        all_versions = await self.get_or_fetch_versions_list(edition)
        all_version_tuples = [(v.major_version, v.minor_version, v.patch_number) for v in all_versions]

        # Split the spec by commas: e.g. "1.14 - 1.16.1, 1.17, 1.19+" has 3 parts
        parts = [part.strip() for part in version_spec.split(",")]

        valid_tuples: list[tuple[int, int, int]] = []
        v_tuple: tuple[int, int, int]
        end_tuple: tuple[Literal["Java", "Bedrock"], int, int, int]

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
                    # Note: the "Java" edition here is just a placeholder for the tuple structure, it is immediately
                    # discarded below in end_tuple[1:]
                    end_tuple = ("Java", major, minor, max_patch)

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
