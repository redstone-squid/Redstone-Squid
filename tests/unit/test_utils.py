"""
Tests for utility functions in squid.db.utils.

This module tests:
1. Version parsing utilities (get_version_string, parse_version_string)
2. Time utilities (utcnow)
3. File upload utilities (upload_to_catbox)
"""

import io
import os
from datetime import datetime, timezone
from typing import Literal
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from squid.db.schema import VersionRecord
from squid.db.utils import get_version_string, parse_version_string, upload_to_catbox, utcnow


@pytest.fixture
def sample_version_record() -> VersionRecord:
    """Sample version record for testing."""
    return {
        "id": 1,
        "edition": "Java",
        "major_version": 1,
        "minor_version": 19,
        "patch_number": 2,
    }


class TestVersionParsing:
    """Tests for version parsing utilities."""

    def test_get_version_string_with_edition(self, sample_version_record: VersionRecord):
        """Test version string generation with edition."""
        result = get_version_string(sample_version_record)
        assert result == "Java 1.19.2"

    def test_get_version_string_without_edition(self, sample_version_record: VersionRecord):
        """Test version string generation without edition."""
        result = get_version_string(sample_version_record, no_edition=True)
        assert result == "1.19.2"

    @pytest.mark.parametrize(
        "version_string,expected",
        [
            ("Java 1.19.2", ("Java", 1, 19, 2)),
            ("1.19.2", ("Java", 1, 19, 2)),  # Default to Java
            ("Bedrock 1.19.2", ("Bedrock", 1, 19, 2)),
            ("JAVA 1.19.2", ("Java", 1, 19, 2)),  # Case-insensitive
            ("bedrock 1.19.2", ("Bedrock", 1, 19, 2)),  # Case-insensitive
        ],
    )
    def test_parse_version_string_valid(
        self, version_string: str, expected: tuple[Literal["Java", "Bedrock"], int, int, int]
    ):
        """Test parsing valid version strings."""
        result = parse_version_string(version_string)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_version",
        [
            "1.19",  # Missing patch
            "Java 1.19",  # Missing patch
            "1",  # Missing minor and patch
            "Java",  # Missing version numbers
            "Other 1.19.2",  # Invalid edition
            "1.19.2.1",  # Extra number
            "",  # Empty string
            "Java 1.19.a",  # Non-numeric patch
        ],
    )
    def test_parse_version_string_invalid(self, invalid_version: str):
        """Test parsing invalid version strings."""
        with pytest.raises(ValueError, match="Invalid version string format"):
            parse_version_string(invalid_version)


class TestTimeUtilities:
    """Tests for time utilities."""

    def test_utcnow_format(self):
        """Test utcnow returns correct format."""
        result = utcnow()
        # Should match format: YYYY-MM-DDThh:mm:ss
        assert len(result) == 19
        assert "T" in result
        assert result[4] == "-" and result[7] == "-"
        assert result[13] == ":" and result[16] == ":"

    def test_utcnow_timezone(self):
        """Test utcnow returns UTC time."""
        with patch("squid.db.utils.datetime") as mock_datetime:
            mock_now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = mock_now
            result = utcnow()
            assert result == "2024-01-01T12:00:00"
            mock_datetime.now.assert_called_once_with(tz=timezone.utc)
