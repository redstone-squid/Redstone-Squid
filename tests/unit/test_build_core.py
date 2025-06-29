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
from unittest.mock import MagicMock

import pytest

from squid.db import DatabaseManager
from squid.db.builds import Build, JoinedBuildRecord
from squid.db.schema import BuildCategory, Door, RestrictionRecord, Status, VersionRecord


@pytest.fixture
def sample_build() -> Build:
    """Sample Build instance for testing."""
    return Build(
        id=1,
        submission_status=Status.PENDING,
        category=BuildCategory.DOOR,
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
    sample_version_json_data: list[VersionRecord], sample_restriction_json_data: list[RestrictionRecord]
) -> JoinedBuildRecord:
    """Sample JoinedBuildRecord for testing."""
    return {  # type: ignore  # This is real data, JoinedBuildRecord uses enums which causes type checking issues
        "id": 172,
        "submission_status": 1,
        "edited_time": "2025-06-09T12:58:32+00:00",
        "record_category": "Fastest",
        "extra_info": {
            "user": "Improved version\n**size**\n22x11x4=968b\n**other info**\nuncontained cus layout big\nfound out it was loc and dir, now it's reliable\nvideo should be less laggy now :)",
            "unknown_patterns": [],
            "unknown_restrictions": {
                "component_restrictions": ["Obsless", "Entityless"],
                "miscellaneous_restrictions": ["Only piston sounds"],
                "wiring_placement_restrictions": ["Unseamless"],
            },
        },
        "width": 22,
        "height": 11,
        "depth": 4,
        "completion_time": None,
        "submission_time": "2025-01-11T15:06:04.110194",
        "category": "Door",
        "submitter_id": 1159485264570359839,
        "ai_generated": True,
        "original_message_id": 1327569309899292754,
        "version_spec": "Java 1.21.1",
        "embedding": None,
        "is_locked": False,
        "locked_at": None,
        "versions": [{"id": 246, "edition": "Java", "patch_number": 1, "major_version": 1, "minor_version": 21}],
        "build_links": [
            {"url": "https://files.catbox.moe/t09cty.png", "build_id": 172, "media_type": "image"},
            {"url": "https://files.catbox.moe/uadbru.mp4", "build_id": 172, "media_type": "video"},
        ],
        "build_creators": [{"user_id": 14, "build_id": 172}, {"user_id": 15, "build_id": 172}],
        "users": [
            {
                "id": 14,
                "ign": "parkertoo",
                "created_at": "2025-01-11T15:06:08.552961",
                "discord_id": None,
                "minecraft_uuid": None,
            },
            {
                "id": 15,
                "ign": "Hammie",
                "created_at": "2025-01-11T15:06:08.934349",
                "discord_id": None,
                "minecraft_uuid": None,
            },
        ],
        "types": [{"id": 1, "name": "Regular", "build_category": "Door"}],
        "restrictions": [
            {"id": 7, "name": "Flush", "type": "wiring-placement", "build_category": "Door"},
            {"id": 43, "name": "Locational", "type": "miscellaneous", "build_category": "Door"},
            {"id": 44, "name": "Directional", "type": "miscellaneous", "build_category": "Door"},
        ],
        "doors": {
            "build_id": 172,
            "door_depth": 1,
            "door_width": 4,
            "door_height": 4,
            "orientation": "Door",
            "normal_closing_time": 5,
            "normal_opening_time": 5,
            "visible_closing_time": None,
            "visible_opening_time": None,
        },
        "extenders": None,
        "utilities": None,
        "entrances": None,
        "messages": {
            "id": 1327569309899292754,
            "content": "Improved version\n## Fastest unseamless 4x4 flush\nby @parkertoo and @Hammie \n**speed**\n0.25s close 0 reset\n0.25s open 0 reset\n\n**size**\n22x11x4=968b\n\n**other info**\nentityless\nuncontained cus layout big\nfound out it was loc and dir, now it's reliable\nonly piston sounds\n**obless**\nvideo should be less laggy now :)",
            "purpose": "build_original_message",
            "build_id": 172,
            "author_id": 1159485264570359839,
            "server_id": 433618741528625152,
            "channel_id": 667401499554611210,
            "updated_at": "2025-06-02T08:23:19.755221+00:00",
            "vote_session_id": None,
        },
    }


@pytest.fixture
def sample_sql_door():
    """Sample SQLAlchemy Door object for testing."""
    # Create a mock Door object with the required attributes
    door = MagicMock(spec=Door)
    door.id = 1
    door.submission_status = Status.PENDING
    door.category = "Door"
    door.record_category = None
    door.width = 5
    door.height = 6
    door.depth = 7
    door.door_width = 2
    door.door_height = 3
    door.door_depth = 1
    door.orientation = "Door"
    door.normal_closing_time = None
    door.normal_opening_time = None
    door.visible_closing_time = None
    door.visible_opening_time = None
    door.extra_info = {}
    door.submitter_id = 1
    door.completion_time = None
    door.edited_time = None
    door.original_message_id = 1234567890
    door.original_message = None
    door.ai_generated = False
    door.embedding = None

    # Mock related objects
    door.types = [MagicMock(name="Regular")]
    door.restrictions = [
        MagicMock(name="No pistons", type="component"),
        MagicMock(name="1-wide", type="wiring-placement"),
    ]
    door.links = [
        MagicMock(url="https://example.com/image.png", media_type="image"),
        MagicMock(url="https://example.com/video.mp4", media_type="video"),
        MagicMock(url="https://example.com/world.zip", media_type="world-download"),
    ]
    door.versions = []
    door.creators = [MagicMock(ign="testuser")]

    return door


def assert_build_attributes(build: Build, expected: dict[str, Any]):
    """Assert that the build attributes are equal to the expected values."""
    for attr, value in expected.items():
        assert getattr(build, attr) == value


class TestBuildConstructors:
    """Tests for Build class constructors."""

    def test_from_json(self, sample_joined_build_record: JoinedBuildRecord):
        """Test build creation from JoinedBuildRecord."""
        build = DatabaseManager().build._from_json(sample_joined_build_record)  # pyright: ignore[reportPrivateUsage]
        assert build is not None


class TestBuildValidation:
    """Tests for Build data validation methods."""

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
