"""Tests for the core functionality of the Build class.

This module tests:
1. Data validation and dimension properties
2. Title generation
3. Build comparison
4. Attribute iteration
"""

import pytest

from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.domain.titles import format_build_category, format_build_display_title
from squid.builds.errors import InvalidBuildError


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


class TestBuildValidation:
    """Tests for Build data validation methods."""

    def test_dimensions_property(self, sample_build: Build):
        """Test dimension property getter/setter round-trip."""
        sample_build.width = 2
        sample_build.height = 3
        sample_build.depth = 1

        assert sample_build.dimensions == (2, 3, 1)

        sample_build.dimensions = (4, 5, 6)
        assert (sample_build.width, sample_build.height, sample_build.depth) == (4, 5, 6)

    def test_door_dimensions_property(self, sample_build: Build):
        """Test door dimension property getter/setter round-trip."""
        sample_build.door_width = 2
        sample_build.door_height = 3
        sample_build.door_depth = 1

        assert sample_build.door_dimensions == (2, 3, 1)

        sample_build.door_dimensions = (4, 5, 6)
        assert (sample_build.door_width, sample_build.door_height, sample_build.door_depth) == (4, 5, 6)


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

    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            (BuildCategory.UTILITY, "Pending: Utility"),
            (BuildCategory.ENTRANCE, "Pending: Entrance"),
            (BuildCategory.OTHER, "Pending: Other"),
        ],
    )
    def test_generic_category_display_titles(self, category: BuildCategory, expected: str) -> None:
        build = Build(category=category, submission_status=Status.PENDING)

        assert build.title == expected

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
