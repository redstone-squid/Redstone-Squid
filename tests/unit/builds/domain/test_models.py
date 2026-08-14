"""Tests for the core functionality of the Build class.

This module tests:
1. Data validation and dimension properties
2. Title generation
3. Build comparison
4. Attribute iteration
"""

import json
from dataclasses import fields as dataclass_fields

import pytest

from squid.builds.domain import (
    Build,
    BuildCategory,
    BuildDraft,
    DoorBuild,
    EntranceBuild,
    ExtenderBuild,
    OtherBuild,
    Status,
    UtilityBuild,
)
from squid.builds.domain.titles import format_build_category, format_build_display_title
from squid.builds.errors import InvalidBuildError


@pytest.fixture
def sample_build() -> DoorBuild:
    """Sample Build instance for testing."""
    build = DoorBuild(
        id=1,
        submission_status=Status.PENDING,
        record_category=None,
        width=5,
        height=6,
        depth=7,
        door_width=2,
        door_height=3,
        door_depth=1,
        patterns=["Regular"],
        orientation="Door",
        wiring_placement_restrictions=["1-wide"],
        component_restrictions=["No pistons"],
        miscellaneous_restrictions=[],
        creators_ign=["testuser"],
        version_spec="1.19+",
        versions=["Java 1.19.0", "Java 1.19.1", "Java 1.19.2", "Java 1.20.0"],
        ai_generated=False,
        extra_info={},
    )
    build.replace_links("image", ["https://example.com/image.png"])
    build.replace_links("video", ["https://example.com/video.mp4"])
    build.replace_links("world-download", ["https://example.com/world.zip"])
    return build


class TestBuildValidation:
    """Tests for Build data validation methods."""

    def test_dimensions_property(self, sample_build: DoorBuild):
        """Test dimension property getter/setter round-trip."""
        sample_build.width = 2
        sample_build.height = 3
        sample_build.depth = 1

        assert sample_build.dimensions == (2, 3, 1)

        sample_build.dimensions = (4, 5, 6)
        assert (sample_build.width, sample_build.height, sample_build.depth) == (4, 5, 6)

    def test_door_dimensions_property(self, sample_build: DoorBuild):
        """Test door dimension property reflects the door fields."""
        sample_build.door_width = 2
        sample_build.door_height = 3
        sample_build.door_depth = 1

        assert sample_build.door_dimensions == (2, 3, 1)

    def test_base_build_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError, match="category subclass"):
            Build()

    def test_category_is_a_fact_of_the_type(self, sample_build: DoorBuild) -> None:
        assert sample_build.category is BuildCategory.DOOR
        assert ExtenderBuild().category is BuildCategory.EXTENDER

    def test_link_helpers_round_trip(self, sample_build: DoorBuild) -> None:
        assert sample_build.image_urls == ("https://example.com/image.png",)
        sample_build.add_link("image", "https://example.com/second.png")
        sample_build.add_link("image", "https://example.com/second.png")  # duplicate ignored
        assert sample_build.image_urls == ("https://example.com/image.png", "https://example.com/second.png")
        sample_build.replace_links("image", ["https://example.com/only.png"])
        assert sample_build.image_urls == ("https://example.com/only.png",)
        # Other media types are untouched by replace_links.
        assert sample_build.video_urls == ("https://example.com/video.mp4",)


class TestBuildDraft:
    """Tests for the pre-category accumulator."""

    def test_finalize_requires_a_category(self) -> None:
        from squid.builds.errors import InvalidBuildError

        with pytest.raises(InvalidBuildError, match="category"):
            BuildDraft().finalize()

    @pytest.mark.parametrize(
        ("category", "expected_type"),
        [
            (BuildCategory.DOOR, DoorBuild),
            (BuildCategory.EXTENDER, ExtenderBuild),
            (BuildCategory.UTILITY, UtilityBuild),
            (BuildCategory.ENTRANCE, EntranceBuild),
            (BuildCategory.OTHER, OtherBuild),
        ],
    )
    def test_finalize_produces_the_category_subclass(self, category: BuildCategory, expected_type: type[Build]) -> None:
        draft = BuildDraft(category=category, width=3, creators_ign=["Alice"])
        build = draft.finalize()
        assert type(build) is expected_type
        assert build.width == 3
        assert build.creators_ign == ["Alice"]

    def test_finalize_applies_door_defaults_explicitly(self) -> None:
        draft = BuildDraft(category=BuildCategory.DOOR)
        build = draft.finalize()
        assert isinstance(build, DoorBuild)
        assert (build.door_width, build.door_height) == (1, 2)
        assert build.orientation == "Door"


class TestBuildTitle:
    """Tests for Build title generation."""

    def test_get_title_basic(self, sample_build: DoorBuild):
        """Test basic title generation."""
        sample_build.submission_status = Status.PENDING
        sample_build.door_width = 2
        sample_build.door_height = 3
        sample_build.door_depth = 1
        sample_build.patterns = ["Regular"]
        sample_build.orientation = "Door"
        sample_build.wiring_placement_restrictions = ["1-wide"]
        sample_build.component_restrictions = ["No pistons"]
        sample_build.ai_generated = False
        assert sample_build.title == "Pending: No pistons 1-wide 2x3 Door"

    def test_display_title_preserves_ux_decorations_and_unknown_markdown(self, sample_build: DoorBuild) -> None:
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
        extender = ExtenderBuild(
            submission_status=Status.CONFIRMED,
            orientation="Upward",
            extension_length=3,
            extender_type="Regular",
            component_restrictions=["Observerless"],
        )

        assert extender.title == "Observerless Upward 3 Piston Extender"

    @pytest.mark.parametrize(
        ("build", "expected"),
        [
            (UtilityBuild(submission_status=Status.PENDING), "Pending: Utility"),
            (EntranceBuild(submission_status=Status.PENDING), "Pending: Entrance"),
            (OtherBuild(submission_status=Status.PENDING), "Pending: Other"),
        ],
    )
    def test_generic_category_display_titles(self, build: Build, expected: str) -> None:
        assert build.title == expected

    def test_search_display_title_is_plain_and_keeps_canonical_data(self, sample_build: DoorBuild) -> None:
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


class TestBuildDiff:
    """Tests for the change summary the vote session persists."""

    def test_diff_reports_only_real_fields(self, sample_build: DoorBuild) -> None:
        other = replace_door(sample_build, door_width=9)
        names = [name for name, _left, _right in sample_build.diff(other)]
        assert names == ["door_width"]
        # Properties are not fields and must not appear as changes.
        assert "dimensions" not in names
        assert "door_dimensions" not in names
        assert "restrictions" not in names
        assert "title" not in names

    def test_diff_renders_links_as_json_safe_url_lists(self, sample_build: DoorBuild) -> None:
        """The vote session stores the result in a JSONB column."""
        other = replace_door(sample_build)
        other.replace_links("image", ["https://example.com/new.png"])

        changes = dict((name, (left, right)) for name, left, right in sample_build.diff(other))
        assert "links" not in changes
        assert changes["image_urls"] == (["https://example.com/image.png"], ["https://example.com/new.png"])
        assert json.dumps(sample_build.diff(other))

    def test_diff_rejects_builds_of_different_categories(self, sample_build: DoorBuild) -> None:
        with pytest.raises(InvalidBuildError, match="different categories"):
            sample_build.diff(UtilityBuild(id=sample_build.id))


def replace_door(build: DoorBuild, **changes: object) -> DoorBuild:
    """A copy of *build* with independent collections."""
    values = {field.name: getattr(build, field.name) for field in dataclass_fields(build)}
    values.update(changes)
    copied = DoorBuild(**values)  # type: ignore[arg-type]
    copied.links = list(build.links)
    return copied
