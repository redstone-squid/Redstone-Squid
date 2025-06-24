"""
Global pytest configuration and shared fixtures.
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import dotenv
import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.compose import DockerCompose
from testcontainers.postgres import PostgresContainer

from squid.db import DatabaseManager
from squid.db.schema import BuildCategory, Restriction, RestrictionRecord, Version, VersionRecord


@pytest.fixture
async def mock_env_vars() -> AsyncGenerator[None, None]:
    """
    Fixture that mocks required environment variables.
    """
    with patch.dict(
        "os.environ",
        {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_KEY": "test-key-123",
            "DATABASE_URL": "postgresql://user:password@localhost:5432/test_db",
            "DB_DRIVER_SYNC": "psycopg2",
            "DB_DRIVER_ASYNC": "asyncpg",
        },
    ):
        yield


@pytest.fixture
async def mock_db_manager(mock_env_vars: None) -> AsyncGenerator[DatabaseManager, None]:
    """Fixture that provides a DatabaseManager instance with mocked SQLAlchemy dependencies."""
    with (
        # Mock the SQLAlchemy engines and sessions
        patch("squid.db.create_async_engine"),
        patch("squid.db.create_engine"),
        patch("squid.db.async_sessionmaker") as mock_async_sessionmaker,
        patch("squid.db.sessionmaker"),
        # Keep the Supabase client mocking for any legacy code
        patch("squid.db.AsyncClient.__init__", return_value=None),
        patch("squid.db.AsyncClient.table") as table_mock,
        patch("squid.db.AsyncClient.rpc", new_callable=AsyncMock),
    ):
        # Mock SQLAlchemy session behavior
        mock_session = AsyncMock(spec=AsyncSession)
        mock_async_sessionmaker.return_value = lambda: mock_session

        # Mock the table behavior (legacy Supabase code)
        table_instance = MagicMock()
        table_instance.select.return_value = table_instance
        table_instance.insert.return_value = table_instance
        table_instance.update.return_value = table_instance
        table_instance.delete.return_value = table_instance
        table_instance.eq.return_value = table_instance
        table_instance.order.return_value = table_instance
        table_instance.execute = AsyncMock()

        table_mock.return_value = table_instance

        DatabaseManager._instance = None  # pyright: ignore[reportPrivateUsage]
        DatabaseManager.version_cache = {}
        yield DatabaseManager()


@pytest.fixture(scope="session")
async def docker_backed_db_manager(mock_env_vars: None) -> AsyncGenerator[DatabaseManager, None]:
    """Create a DatabaseManager instance using a Docker-backed Supabase container.

    This fixture is used for integration tests that require a real database connection.
    As creating a Supabase container can be time-consuming, it is scoped to the session.
    """
    with DockerCompose(
        "supabase/docker/", compose_file_name=["docker-compose.yml"], pull=True, env_file=".env"
    ) as compose:
        envs = dotenv.dotenv_values(compose.env_file)
        yield DatabaseManager(supabase_url=envs["API_EXTERNAL_URL"], supabase_key=envs["SERVICE_ROLE_KEY"])


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def pg_only_db_manager() -> AsyncGenerator[DatabaseManager, None]:
    """Create a DatabaseManager instance using a Docker-backed PostgreSQL container.

    This fixture provides a real PostgreSQL database connection while mocking the AsyncClient
    components. Useful for tests that need real database operations but don't require
    Supabase-specific functionality.
    """
    with PostgresContainer("postgres:17") as postgres:
        database_url = postgres.get_connection_url()
        
        # Apply all migrations before yielding
        migrations_dir = Path(__file__).parent.parent / "supabase" / "migrations"
        migration_files = sorted(migrations_dir.glob("*.sql"))
        
        # Connect to the database and apply migrations
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        
        try:
            with conn.cursor() as cursor:
                for migration_file in migration_files:
                    print(f"Applying migration: {migration_file.name}")
                    migration_sql = migration_file.read_text(encoding="utf-8")
                    cursor.execute(migration_sql)
        finally:
            conn.close()
        
        with (
            # Mock the AsyncClient components while keeping real database connections
            patch("squid.db.AsyncClient.__init__", return_value=None),
            patch("squid.db.AsyncClient.table") as table_mock,
            patch("squid.db.AsyncClient.rpc", new_callable=AsyncMock),
        ):
            # Mock the table behavior for legacy Supabase code
            table_instance = MagicMock()
            table_instance.select.return_value = table_instance
            table_instance.insert.return_value = table_instance
            table_instance.update.return_value = table_instance
            table_instance.delete.return_value = table_instance
            table_instance.eq.return_value = table_instance
            table_instance.order.return_value = table_instance
            table_instance.execute = AsyncMock()
            
            table_mock.return_value = table_instance
            
            # Reset singleton state
            DatabaseManager._instance = None  # pyright: ignore[reportPrivateUsage]
            DatabaseManager.version_cache = {}
            
            yield DatabaseManager(database_url=database_url)


@pytest.fixture
def sample_version_json_data() -> list[VersionRecord]:
    """Sample Minecraft version data for testing."""
    return [
        {"id": 1, "edition": "Java", "major_version": 1, "minor_version": 14, "patch_number": 0},
        {"id": 2, "edition": "Java", "major_version": 1, "minor_version": 15, "patch_number": 0},
        {"id": 3, "edition": "Java", "major_version": 1, "minor_version": 16, "patch_number": 0},
        {"id": 4, "edition": "Java", "major_version": 1, "minor_version": 16, "patch_number": 1},
        {"id": 5, "edition": "Java", "major_version": 1, "minor_version": 17, "patch_number": 0},
        {"id": 6, "edition": "Java", "major_version": 1, "minor_version": 17, "patch_number": 1},
        {"id": 7, "edition": "Java", "major_version": 1, "minor_version": 18, "patch_number": 0},
        {"id": 8, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 0},
        {"id": 9, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 1},
        {"id": 10, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 2},
        {"id": 11, "edition": "Java", "major_version": 1, "minor_version": 20, "patch_number": 0},
    ]


@pytest.fixture
def sample_restriction_json_data() -> list[RestrictionRecord]:
    """Sample restriction data for testing."""
    return [
        {"id": 1, "name": "No pistons", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 2, "name": "No observers", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 3, "name": "No redstone dust", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 4, "name": "1-wide", "type": "wiring-placement", "build_category": BuildCategory.DOOR},
        {"id": 5, "name": "2-wide", "type": "miscellaneous", "build_category": BuildCategory.DOOR},
    ]


@pytest.fixture
def sample_version_data() -> list[Version]:
    """Sample Minecraft version data for testing."""
    versions = [
        Version(edition="Java", major_version=1, minor_version=14, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=15, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=16, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=16, patch_number=1),
        Version(edition="Java", major_version=1, minor_version=17, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=17, patch_number=1),
        Version(edition="Java", major_version=1, minor_version=18, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=19, patch_number=0),
        Version(edition="Java", major_version=1, minor_version=19, patch_number=1),
        Version(edition="Java", major_version=1, minor_version=19, patch_number=2),
        Version(edition="Java", major_version=1, minor_version=20, patch_number=0),
    ]
    for i, version in enumerate(versions):
        version.id = i + 1
    return versions


@pytest.fixture
def sample_restriction_data() -> list[Restriction]:
    """Sample restriction data for testing."""
    restrictions = [
        Restriction(name="No pistons", type="component", build_category="Door"),
        Restriction(name="No observers", type="component", build_category="Door"),
        Restriction(name="No redstone dust", type="component", build_category="Door"),
        Restriction(name="1-wide", type="wiring-placement", build_category="Door"),
        Restriction(name="2-wide", type="miscellaneous", build_category="Door"),
    ]
    for i, restriction in enumerate(restrictions):
        restriction.id = i + 1
    return restrictions
