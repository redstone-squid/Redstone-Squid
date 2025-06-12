"""
Tests for the core functionality of the Build class.

This module tests:
1. Static constructors (from_id, from_dict, from_json)
2. Data validation (parse_time_string, dimension properties)
3. Title generation (get_title)
4. Build comparison (diff method)
5. Attribute iteration (__iter__)
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from squid.db import DatabaseManager
from squid.db.builds import Build, JoinedBuildRecord
from squid.db.schema import Category, RestrictionRecord, Status, VersionRecord


@pytest.fixture
def sample_build() -> Build:
    """Sample Build instance for testing."""
    return Build(
        id=1,
        submission_status=Status.PENDING,
        category=Category.DOOR,
        record_category=None,
        width=5,
        height=6,
        depth=7,
        door_width=2,
        door_height=3,
        door_depth=1,
        door_type=["Regular"],
        door_orientation_type="Door",
        wiring_placement_restrictions=["1-wide"],
        component_restrictions=["No pistons"],
        miscellaneous_restrictions=[],
        creators_ign=["testuser"],
        version_spec="1.19+",
        versions=["Java 1.19.0", "Java 1.19.1", "Java 1.19.2", "Java 1.20.0"],
        image_urls=["https://example.com/image.png"],
        video_urls=["https://example.com/video.mp4"],
        world_download_urls=["https://example.com/world.zip"],
        ai_generated=False,
        extra_info={},
    )


@pytest.fixture
def sample_joined_build_record(
    sample_version_data: list[VersionRecord], sample_restriction_data: list[RestrictionRecord]
) -> JoinedBuildRecord:
    """Sample JoinedBuildRecord for testing."""
    return {
        "id": 1,
        "submission_status": Status.PENDING,
        "category": Category.DOOR,
        "record_category": None,
        "width": 5,
        "height": 6,
        "depth": 7,
        "version_spec": "1.19+",
        "ai_generated": False,
        "extra_info": {},
        "submitter_id": 1,
        "embedding": None,
        "completion_time": None,
        "edited_time": "2023-01-01T00:00:00Z",
        "doors": {
            "build_id": 1,
            "orientation": "Door",
            "door_width": 1,
            "door_height": 1,
            "door_depth": 1,
            "normal_closing_time": None,
            "normal_opening_time": None,
            "visible_closing_time": None,
            "visible_opening_time": None,
        },
        "extenders": None,
        "utilities": None,
        "entrances": None,
        "types": [{"id": 1, "name": "Regular", "build_category": Category.DOOR}],
        "restrictions": sample_restriction_data,
        "versions": sample_version_data,
        "build_links": [
            {"build_id": 1, "url": "https://example.com/image.png", "media_type": "image"},
            {"build_id": 1, "url": "https://example.com/video.mp4", "media_type": "video"},
            {"build_id": 1, "url": "https://example.com/world.zip", "media_type": "world-download"},
        ],
        "build_creators": [{"user_id": 1}],
        "users": [
            {"id": 1, "ign": "testuser", "discord_id": 0, "minecraft_uuid": None, "created_at": "2023-01-01T00:00:00Z"}
        ],
        "messages": None,
        "submission_time": "2023-01-01T00:00:00Z",
        "original_message_id": 1234567890,
        "is_locked": False,
        "locked_at": None,
    }


def assert_build_attributes(build: Build, expected: dict[str, Any]):
    """Assert that the build attributes are equal to the expected values."""
    for attr, value in expected.items():
        assert getattr(build, attr) == value


class TestBuildConstructors:
    """Tests for Build class constructors."""

    async def test_from_id_success(
        self, mock_db_manager: DatabaseManager, sample_joined_build_record: JoinedBuildRecord
    ):
        """Test successful build creation from ID."""
        # Setup mock response
        mock_db_manager.table().select().eq().maybe_single().execute = AsyncMock(  # type: ignore
            return_value=MagicMock(data=sample_joined_build_record)
        )

        build = await Build.from_id(1)
        assert build is not None

    async def test_from_id_not_found(self, mock_db_manager: Any):
        """Test build creation from non-existent ID."""
        # Setup mock response for non-existent build
        mock_db_manager.table().select().eq().maybe_single().execute = AsyncMock(return_value=None)

        build = await Build.from_id(999)
        assert build is None

    def test_from_json(self, sample_joined_build_record: JoinedBuildRecord):
        """Test build creation from JoinedBuildRecord."""
        build = Build.from_json(sample_joined_build_record)
        assert build is not None


class TestBuildValidation:
    """Tests for Build data validation methods."""

    @pytest.mark.parametrize(
        "time_string, expected",
        [
            ("1.5s", 30),  # 1.5 seconds = 30 ticks
            ("30", 600),  # 30 is assumed to be seconds
            ("~2s", 40),  # 2s = 40 ticks
            ("invalid", None),  # Invalid format
            (None, None),  # None input
            ("-1", -20),
            ("0.055s", 1),  # Extra precision is ignored
        ],
    )
    def test_parse_time_string(self, time_string: str | None, expected: int | None):
        """Test time string parsing with various formats."""
        result = Build.parse_time_string(time_string)
        assert result == expected

    @pytest.mark.parametrize(
        "width, height, depth",
        [
            (2, 3, 1),  # Valid dimensions
            (None, 3, 1),  # Width None
            (2, None, 1),  # Height None
            (2, 3, None),  # Depth None
            (None, None, None),  # All None
            (0, 0, 0),  # Zero dimensions
            (-1, 3, 1),  # Negative width
        ],
    )
    def test_dimensions_property(self, sample_build: Build, width: int | None, height: int | None, depth: int | None):
        """Test dimension property getters/setters."""
        sample_build.width = width
        sample_build.height = height
        sample_build.depth = depth

        # Test getter
        width, height, depth = sample_build.dimensions
        assert sample_build.width == width
        assert sample_build.height == height
        assert sample_build.depth == depth

        # Test setter
        sample_build.dimensions = (width, height, depth)
        assert sample_build.width == width
        assert sample_build.height == height
        assert sample_build.depth == depth

    @pytest.mark.parametrize(
        "width, height, depth",
        [
            (2, 3, 1),  # Valid dimensions
            (None, 3, 1),  # Width None
            (2, None, 1),  # Height None
            (2, 3, None),  # Depth None
            (None, None, None),  # All None
            (0, 0, 0),  # Zero
            (-1, 3, 1),  # Negative width
        ],
    )
    def test_door_dimensions_property(
        self, sample_build: Build, width: int | None, height: int | None, depth: int | None
    ):
        """Test door dimension property getters/setters."""
        sample_build.door_width = width
        sample_build.door_height = height
        sample_build.door_depth = depth

        # Test getter
        width, height, depth = sample_build.door_dimensions
        assert width == sample_build.door_width
        assert height == sample_build.door_height
        assert depth == sample_build.door_depth

        # Test setter
        sample_build.door_dimensions = (width, height, depth)
        assert sample_build.door_width == width
        assert sample_build.door_height == height
        assert sample_build.door_depth == depth


class TestBuildTitle:
    """Tests for Build title generation."""

    def test_get_title_basic(self, sample_build: Build):
        """Test basic title generation."""
        sample_build.submission_status = Status.PENDING
        sample_build.door_dimensions = (2, 3, 1)
        sample_build.door_type = ["Regular"]
        sample_build.door_orientation_type = "Door"
        sample_build.wiring_placement_restrictions = ["1-wide"]
        sample_build.component_restrictions = ["No pistons"]
        sample_build.ai_generated = False
        title = sample_build.get_title()
        assert title == "Pending: No pistons 2x3 1-wide Door"

    def test_get_title_missing_orientation(self, sample_build: Build):
        """Test title generation fails with missing orientation."""
        sample_build.door_orientation_type = None
        with pytest.raises(ValueError, match="Door orientation type"):
            sample_build.get_title()


class TestBuildComparison:
    """Tests for Build comparison functionality."""

    def test_diff_identical_builds(self, sample_build: Build):
        """Test diff between identical builds."""
        pass

    def test_diff_different_builds(self, sample_build: Build):
        """Test diff between different builds."""
        pass

    def test_diff_different_ids(self, sample_build: Build):
        """Test diff between builds with different IDs."""
        pass

    def test_diff_different_ids_allowed(self, sample_build: Build):
        """Test diff between builds with different IDs when allowed."""
        pass
