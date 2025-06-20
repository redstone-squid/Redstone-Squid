from unittest.mock import patch

import pytest
from postgrest.base_request_builder import APIResponse

from squid.db import DatabaseManager
from squid.db.schema import RestrictionRecord, VersionRecord


@pytest.mark.unit
class TestDatabaseManager:
    """
    Test suite for DatabaseManager core functionality.

    Following Phase 1.1 of the testing plan, focusing on:
    - Version caching
    - Version specification parsing
    - Restriction handling
    """

    async def test_version_caching(
        self, mock_db_manager: DatabaseManager, sample_version_json_data: list[VersionRecord]
    ) -> None:
        """Test that version list is properly cached."""
        mock_db_manager.table().select().execute.return_value = APIResponse(  # type: ignore
            data=sample_version_json_data, count=len(sample_version_json_data)
        )
        # First call should query the database
        versions1 = await mock_db_manager.get_or_fetch_versions_list(edition="Java")
        assert versions1 == sample_version_json_data

        # Second call should use cached data
        versions2 = await mock_db_manager.get_or_fetch_versions_list(edition="Java")
        assert versions2 == sample_version_json_data

        # Verify database was only queried once
        mock_db_manager.table().select().execute.assert_called_once()  # type: ignore

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
        self, mock_db_manager: DatabaseManager, sample_version_json_data: list[VersionRecord], spec: str, expected: list[str]
    ) -> None:
        """Test version specification parsing with different formats."""
        # Patch get_or_fetch_versions_list
        with patch.object(mock_db_manager, "get_or_fetch_versions_list", return_value=sample_version_json_data):
            result = await mock_db_manager.find_versions_from_spec(spec)
            assert sorted(result) == sorted(expected)

    async def test_restriction_caching(
        self, mock_db_manager: DatabaseManager, sample_restriction_json_data: list[RestrictionRecord]
    ) -> None:
        """Test that restrictions are properly cached."""
        mock_db_manager.table().select().execute.return_value = APIResponse(  # type: ignore
            data=sample_restriction_json_data, count=len(sample_restriction_json_data)
        )

        # First call should query the database
        restrictions1 = await mock_db_manager.fetch_all_restrictions()
        assert restrictions1 == sample_restriction_json_data

        # Second call should use cached data
        restrictions2 = await mock_db_manager.fetch_all_restrictions()
        assert restrictions2 == sample_restriction_json_data

        # Verify database was only queried once
        mock_db_manager.table().select().execute.assert_called_once()  # type: ignore

    async def test_fetch_all_restrictions(
        self, mock_db_manager: DatabaseManager, sample_restriction_json_data: list[RestrictionRecord]
    ) -> None:
        # Implementation of the new test function
        pass

    async def test_find_versions_from_spec_simple(
        self, mock_db_manager: DatabaseManager, sample_version_json_data: list[VersionRecord], spec: str, expected: list[str]
    ) -> None:
        # Implementation of the new test function
        pass

    async def test_get_or_fetch_versions_list(
        self, mock_db_manager: DatabaseManager, sample_restriction_json_data: list[RestrictionRecord]
    ) -> None:
        # Implementation of the new test function
        pass
