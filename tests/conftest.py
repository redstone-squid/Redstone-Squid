"""
Global pytest configuration and shared fixtures.
"""
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import dotenv
import pytest
from postgrest.base_request_builder import APIResponse
from testcontainers.compose import DockerCompose

from squid.db import DatabaseManager
from squid.db.schema import Restriction, RestrictionRecord, Version, VersionRecord, BuildCategory


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
    """Fixture that provides a DatabaseManager instance with mocked dependencies."""
    with (
        patch("squid.db.AsyncClient.__init__", return_value=None),
        patch("squid.db.AsyncClient.table") as table_mock,
        patch("squid.db.AsyncClient.rpc", new_callable=AsyncMock),
    ):
        table_instance = MagicMock()
        table_instance.select.return_value = table_instance
        table_instance.insert.return_value = table_instance
        table_instance.update.return_value = table_instance
        table_instance.delete.return_value = table_instance
        table_instance.eq.return_value = table_instance
        table_instance.order.return_value = table_instance
        table_instance.execute = AsyncMock(return_value=APIResponse(data=[], count=0))

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
