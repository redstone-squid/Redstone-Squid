"""Tests for the core functionality of the Build class.

This module tests:
1. Data validation and dimension properties
2. Title generation
3. Build comparison
4. Attribute iteration
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.domain.titles import format_build_category, format_build_display_title
from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.models import Door


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


class TestBuildValidation:
    """Tests for Build data validation methods."""

    @pytest.mark.parametrize(
        ("width", "height", "depth"),
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
        ("width", "height", "depth"),
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
        assert sample_build.title == "Pending: No pistons 1-wide 2x3 Door"

    def test_get_title_missing_orientation(self, sample_build: Build):
        """Test title generation fails with missing orientation."""
        sample_build.door_orientation_type = None
        with pytest.raises(InvalidBuildError, match="Door orientation type"):
            _ = sample_build.title

    def test_display_title_preserves_ux_decorations_and_unknown_markdown(self, sample_build: Build) -> None:
        sample_build.ai_generated = True
        sample_build.animated_restrictions = ["Symmetrical"]
        sample_build.extra_info = {
            "unknown_patterns": ["Mystery Shape"],
            "unknown_restrictions": {
                "miscellaneous_restrictions": ["0.3s", "524 Blocks"],
                "component_restrictions": ["Mysteryless"],
                "wiring_placement_restrictions": ["Odd Wiring"],
            },
        }

        assert sample_build.title == (
            "Pending: 🤖 0.3s 524 Blocks No pistons *Mysteryless* "
            "1-wide *Odd Wiring* Symmetrical 2x3 *Mystery Shape* Door"
        )

    def test_extender_display_title(self) -> None:
        extender = Build(
            category=BuildCategory.EXTENDER,
            submission_status=Status.CONFIRMED,
            extender_orientation="Upward",
            extension_length=3,
            extender_type="Regular",
            component_restrictions=["Observerless"],
        )

        assert extender.title == "Observerless Upward 3 Piston Extender"

    def test_search_display_title_is_plain_and_keeps_canonical_data(self, sample_build: Build) -> None:
        sample_build.versions = ["Java 1.20.0"]
        sample_build.extra_info = {
            "unknown_restrictions": {
                "component_restrictions": ["Mysteryless"],
                "wiring_placement_restrictions": ["Odd Wiring"],
            }
        }

        formatted = format_build_category(sample_build)
        display = format_build_display_title(sample_build, markdown=False, current_version="Java 1.21.0")

        assert formatted.title == "1-wide Odd Wiring 2x3 Door"
        assert formatted.subtitle == "Mysteryless No pistons"
        assert display == "Pending: No pistons Mysteryless 1-wide Odd Wiring 2x3 Door [BROKEN]"
        assert "*" not in display


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
