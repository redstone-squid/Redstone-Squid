from unittest.mock import AsyncMock, Mock, patch

import pytest

from squid.db import BuildTagsManager, DatabaseManager
from squid.db.schema import Restriction


@pytest.mark.unit
class TestDatabaseManager:
    """
    Test suite for DatabaseManager core functionality.

    Legacy database facade coverage for restriction access.
    """

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
