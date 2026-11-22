"""Process-level SQLAlchemy engine and session infrastructure."""

from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from squid import PROJECT_ROOT
from squid.config import DatabaseConfig
from squid.core.errors import DataIntegrityError


class DatabaseEngine:
    """Own the async PostgreSQL connection pool and session factory for one process."""

    def __init__(
        self,
        config: DatabaseConfig,
        *,
        debug: bool = False,
    ) -> None:
        """Create an asyncpg connection pool for the configured database.

        Args:
            config: Database connection settings.
            debug: Whether to log SQL statements.
        """
        base = make_url(config.url.get_secret_value())
        self.async_engine: AsyncEngine = create_async_engine(base.set(drivername="postgresql+asyncpg"), echo=debug)
        self.async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.async_engine, expire_on_commit=False
        )

    async def close(self) -> None:
        """Release the database connection pool."""
        await self.async_engine.dispose()

    async def ping(self) -> None:
        """Execute a lightweight query to verify the async connection path."""
        async with self.async_session() as session:
            await session.execute(select(1))

    async def check_readiness(self) -> None:
        """Verify connectivity and that the deployed schema is at this release's head."""
        async with self.async_session() as session:
            versions = frozenset((await session.scalars(text("SELECT version_num FROM alembic_version"))).all())
        expected = expected_migration_heads()
        if versions != expected:
            msg = "The database migration revision does not match this application release."
            raise DataIntegrityError(
                msg,
                context={"database_heads": sorted(versions), "application_heads": sorted(expected)},
                developer_action="Run the release migration job before making the service ready.",
            )


def migration_heads(root: Path = PROJECT_ROOT) -> frozenset[str]:
    """Return the migration heads in the source tree at `root`."""
    config = Config(toml_file=root / "pyproject.toml")
    return frozenset(ScriptDirectory.from_config(config).get_heads())


@cache
def expected_migration_heads() -> frozenset[str]:
    """Read the migration heads shipped in this source/image exactly once."""
    return migration_heads()
