"""Build taxonomy adapter tests."""

from unittest.mock import AsyncMock, Mock

from squid.builds.infrastructure.taxonomy import BuildTagsManager
from squid.tags.infrastructure.models import TagDefinition


class TestBuildTagsManager:
    """Test suite for BuildTagsManager restriction access."""

    async def test_fetch_all_restrictions(self, sample_restriction_data: list[TagDefinition]) -> None:
        """Test fetching all restrictions returns expected data."""
        mock_session = AsyncMock()
        mock_session_maker = Mock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = Mock()
        mock_session.execute.return_value = mock_result

        mock_scalars = Mock()
        mock_result.scalars.return_value = mock_scalars
        mock_scalars.all.return_value = sample_restriction_data

        build_tags = BuildTagsManager(mock_session_maker)

        restrictions = await build_tags.fetch_all_restrictions()
        assert restrictions == sample_restriction_data
