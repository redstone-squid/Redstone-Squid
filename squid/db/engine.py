"""Process-level SQLAlchemy engine and session infrastructure."""

import os

from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from squid.db.inspect_db import is_sane_database


class DatabaseEngine:
    """Owns the process-wide SQLAlchemy engines and session factories."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        debug: bool = False,
    ) -> None:
        """Initializes the engines and session factories.

        Args:
            database_url: The database connection string. Falls back to the
                DATABASE_URL environment variable if not given.
            debug: Whether to echo SQL statements, for debugging.
        """
        database_url = database_url or os.environ.get("DATABASE_URL")
        driver_sync = os.environ.get("DB_DRIVER_SYNC")
        driver_async = os.environ.get("DB_DRIVER_ASYNC")

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

        base = make_url(database_url)
        self.async_engine: AsyncEngine = create_async_engine(
            base.set(drivername=f"{base.drivername}+{driver_async}"), echo=debug
        )
        self.async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.async_engine, expire_on_commit=False
        )
        self.sync_engine: Engine = create_engine(base.set(drivername=f"{base.drivername}+{driver_sync}"), echo=debug)
        self.sync_session: sessionmaker[Session] = sessionmaker(self.sync_engine, expire_on_commit=False)

    async def close(self) -> None:
        """Release database connection pools owned by this engine."""
        await self.async_engine.dispose()
        self.sync_engine.dispose()

    def validate_database_consistency(self, base_cls: type[DeclarativeBase]) -> None:
        """Validates that the database schema is consistent with the expected schema."""
        if not is_sane_database(base_cls, self.sync_engine):
            msg = "The database schema is not consistent with the expected schema."
            raise RuntimeError(msg)
