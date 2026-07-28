"""
Handles database interactions for the bot.

Essentially a wrapper around the Supabase client and python bindings so that the bot part of the code doesn't have to deal with the specifics of the database.
"""

import os
from typing import Any, ClassVar

from sqlalchemy import create_engine, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from supabase._async.client import AsyncClient
from supabase.lib.client_options import AsyncClientOptions

from squid.db.build_manager import BuildManager
from squid.db.build_tags import BuildTagsManager
from squid.db.inspect_db import is_sane_database
from squid.db.repos.message_repository import MessageRepository
from squid.db.repos.user_repository import UserRepository
from squid.db.server_settings import ServerSettingManager


class DatabaseManager(AsyncClient):
    """Process-wide database infrastructure and repository owner."""

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
            msg = (
                "supabase_url not given and no SUPABASE_URL environmental variable found. "
                "Specify SUPABASE_URL either with a .env file or a SUPABASE_URL environment variable."
            )
            raise RuntimeError(msg)
        if not supabase_key:
            msg = (
                "supabase_key not given and no SUPABASE_KEY environmental variable found. "
                "Specify SUPABASE_KEY either with a .env file or a SUPABASE_KEY environment variable."
            )
            raise RuntimeError(msg)
        if not database_url:
            msg = (
                "database_url not given and no DATABASE_URL environmental variable found. "
                "Specify DATABASE_URL either with a .env file or a DATABASE_URL environment variable."
            )
            raise RuntimeError(msg)
        if not driver_sync:
            msg = "No DB_DRIVER_SYNC environment variable found."
            raise RuntimeError(msg)
        if not driver_async:
            msg = "No DB_DRIVER_ASYNC environment variable found."
            raise RuntimeError(msg)

        # Initialize Supabase client
        super().__init__(supabase_url, supabase_key, options)

        # Initialize SQLAlchemy engine and session maker
        base = make_url(database_url)
        self.async_engine = create_async_engine(base.set(drivername=f"{base.drivername}+{driver_async}"), echo=debug)
        self.async_session = async_sessionmaker(self.async_engine, expire_on_commit=False)
        self.sync_engine = create_engine(base.set(drivername=f"{base.drivername}+{driver_sync}"), echo=debug)
        self.sync_session = sessionmaker(self.sync_engine, expire_on_commit=False)

        # Initialize repositories
        self.message_repo = MessageRepository(self.async_session)
        self.user_repo = UserRepository(self.async_session)

        # Initialize managers
        self.server_setting = ServerSettingManager(self.async_session)
        self.build_tags = BuildTagsManager(self.async_session)
        self.build = BuildManager(self.async_session)

    async def close(self) -> None:
        """Release database connection pools owned by this manager."""
        await self.async_engine.dispose()
        self.sync_engine.dispose()
        if type(self)._instance is self:
            type(self)._instance = None

    def validate_database_consistency(self, base_cls: type[DeclarativeBase]) -> None:
        """Validates that the database schema is consistent with the expected schema."""
        if not is_sane_database(base_cls, self.sync_engine):
            msg = "The database schema is not consistent with the expected schema."
            raise RuntimeError(msg)
