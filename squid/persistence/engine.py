"""Process-level SQLAlchemy engine and session infrastructure."""

from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from squid.config import DatabaseConfig
from squid.core.errors import DataIntegrityError
from squid.persistence.inspection import is_sane_database


class DatabaseEngine:
    """Owns the process-wide SQLAlchemy engines and session factories."""

    def __init__(
        self,
        config: DatabaseConfig,
        *,
        debug: bool = False,
    ) -> None:
        """Initializes the engines and session factories.

        Args:
            config: Database connection URL and drivers.
            debug: Whether to echo SQL statements, for debugging.
        """
        base = make_url(config.url.get_secret_value())
        self.async_engine: AsyncEngine = create_async_engine(base.set(drivername="postgresql+asyncpg"), echo=debug)
        self.async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.async_engine, expire_on_commit=False
        )
        self.sync_engine: Engine = create_engine(base.set(drivername="postgresql+psycopg2"), echo=debug)
        self.sync_session: sessionmaker[Session] = sessionmaker(self.sync_engine, expire_on_commit=False)

    async def close(self) -> None:
        """Release database connection pools owned by this engine."""
        await self.async_engine.dispose()
        self.sync_engine.dispose()

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

    def validate_database_consistency(self, base_cls: type[DeclarativeBase]) -> None:
        """Validates that the database schema is consistent with the expected schema."""
        if not is_sane_database(base_cls, self.sync_engine):
            msg = "The database schema is not consistent with the expected schema."
            raise DataIntegrityError(msg)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""Source tree root, where `alembic.ini` and the `alembic/` script directory live."""


def migration_heads(root: Path = PROJECT_ROOT) -> frozenset[str]:
    """Heads of the migration scripts under `root`.

    `root` is a parameter because `check_readiness` compares the deployed revision against this
    set: a test can otherwise only assert the mismatch branch, never a passing readiness check
    against a known head.
    """
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


@cache
def expected_migration_heads() -> frozenset[str]:
    """Read the migration heads shipped in this source/image exactly once."""
    return migration_heads()
