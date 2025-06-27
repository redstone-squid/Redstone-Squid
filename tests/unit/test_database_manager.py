from unittest.mock import AsyncMock, Mock, patch

import pytest

from squid.db import DatabaseManager, BuildTagsManager
from squid.db.schema import Restriction, Version


@pytest.mark.unit
class TestDatabaseManager:
    """
    Test suite for DatabaseManager core functionality.

    Following Phase 1.1 of the testing plan, focusing on:
    - Version caching
    - Version specification parsing
    - Restriction handling
    """

    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("1.14 - 1.16.1", ["Java 1.14.0", "Java 1.15.0", "Java 1.16.0", "Java 1.16.1"]),
            ("1.19+", ["Java 1.19.0", "Java 1.19.1", "Java 1.19.2", "Java 1.20.0"]),
            ("1.17.1", ["Java 1.17.1"]),
            ("Java 1.16", ["Java 1.16.0", "Java 1.16.1"]),
        ],
    )
    async def test_version_spec_parsing(
        self,
        mock_db_manager: DatabaseManager,
        sample_version_data: list[Version],
        spec: str,
        expected: list[str],
    ) -> None:
        """Test version specification parsing with different formats."""
        # Patch get_or_fetch_versions_list to return Version objects instead of dicts
        with patch.object(mock_db_manager, "get_or_fetch_versions_list", return_value=sample_version_data):
            result = await mock_db_manager.find_versions_from_spec(spec)
            assert sorted(result) == sorted(expected)

    async def test_fetch_all_restrictions(
        self, mock_db_manager: DatabaseManager, sample_restriction_data: list[Restriction]
    ) -> None:
        """Test fetching all restrictions returns expected data."""
        with patch.object(mock_db_manager, "async_session") as mock_session_maker:
            mock_session = AsyncMock()
            mock_session_maker.return_value.__aenter__.return_value = mock_session

            mock_result = Mock()
            mock_session.execute.return_value = mock_result

            mock_scalars = Mock()
            mock_result.scalars.return_value = mock_scalars

            mock_scalars.all.return_value = sample_restriction_data

            mock_db_manager.build_tags = BuildTagsManager(mock_session_maker)

            restrictions = await mock_db_manager.build_tags.fetch_all_restrictions()
            assert restrictions == sample_restriction_data

    async def test_get_or_fetch_versions_list(
        self, mock_db_manager: DatabaseManager, sample_version_data: list[Version]
    ) -> None:
        """Test fetching version list returns expected data."""
        with patch.object(mock_db_manager, "async_session") as mock_session_maker:
            mock_session = AsyncMock()
            mock_session_maker.return_value.__aenter__.return_value = mock_session

            mock_result = Mock()
            mock_scalars = Mock()
            mock_scalars.all.return_value = sample_version_data
            mock_result.scalars.return_value = mock_scalars
            mock_session.execute.return_value = mock_result

            versions = await mock_db_manager.get_or_fetch_versions_list(edition="Java")
            assert versions == sample_version_data
