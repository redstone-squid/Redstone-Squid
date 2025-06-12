"""
Global pytest configuration and shared fixtures.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from postgrest import APIResponse

from squid.db import DatabaseManager
from squid.db.schema import RestrictionRecord, VersionRecord


@pytest.fixture
async def mock_env_vars() -> AsyncGenerator[None, None]:
    """
    Fixture that mocks required environment variables.
    """
    with patch.dict("os.environ", {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_KEY": "test-key-123"}):
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

        DatabaseManager._instance = None
        DatabaseManager.version_cache = {}
        yield DatabaseManager()


@pytest.fixture
def sample_version_data() -> list[VersionRecord]:
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
def sample_restriction_data() -> list[RestrictionRecord]:
    """Sample restriction data for testing."""
    return [
        {"id": 1, "name": "No pistons", "type": "component", "build_category": "Door"},
        {"id": 2, "name": "No observers", "type": "component", "build_category": "Door"},
        {"id": 3, "name": "No redstone dust", "type": "component", "build_category": "Door"},
        {"id": 4, "name": "1-wide", "type": "wiring-placement", "build_category": "Door"},
        {"id": 5, "name": "2-wide", "type": "miscellaneous", "build_category": "Door"},
    ]
