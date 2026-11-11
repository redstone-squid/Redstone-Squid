"""Typed build-edit patch composition."""

from copy import deepcopy

import pytest

from squid.builds.application import BuildEditPatch
from squid.builds.errors import InvalidBuildError
from squid.builds.models import DoorBuild, ExtenderBuild, UtilityBuild
from squid.core.sentinel import UNSET


def test_combine_preserves_every_typed_patch_field() -> None:
    patch = BuildEditPatch(
        extender_orientation="Up",
        extension_length=5,
        display_name="My build",
        visible_opening_time=3,
        visible_closing_time=4,
        version_spec="1.21",
        dimensions=(1, 2, 3),
        door_dimensions=(4, 5, 6),
        door_type=["Full lamp"],
        door_orientation_type="Door",
        wiring_placement_restrictions=["Seamless"],
        animated_restrictions=["Full sync"],
        component_restrictions=["Observerless"],
        miscellaneous_restrictions=["Directional"],
        locationality="Locational",
        directionality="Directional",
        normal_closing_time=7,
        normal_opening_time=8,
        extra_user_info="notes",
        creators_ign=["Alice"],
        image_urls=["https://example.com/image.png"],
        video_urls=["https://example.com/video.mp4"],
        world_download_urls=["https://example.com/world.zip"],
        schematic_urls=["https://example.com/build.litematic"],
        render_urls=["https://example.com/render.png"],
        server_ip="play.example.com",
        coordinates="1 2 3",
        command_to_get_to_build="/warp door",
        completion_time="2026-08-30",
        extra_info={"server_info": {"server_ip": "play.example.com"}},
        ai_generated=False,
    )

    assert BuildEditPatch.combine([patch]) == patch


def test_combine_prefers_the_latest_fragment() -> None:
    combined = BuildEditPatch.combine(
        [BuildEditPatch(version_spec="1.20"), BuildEditPatch(image_urls=["old"]), BuildEditPatch(version_spec="1.21")]
    )

    assert combined == BuildEditPatch(version_spec="1.21", image_urls=["old"])


def test_copied_patch_does_not_overwrite_omitted_fields() -> None:
    original = BuildEditPatch(version_spec="1.21", normal_opening_time=8)
    incoming = deepcopy(BuildEditPatch(normal_opening_time=None))

    assert incoming.version_spec is UNSET
    assert BuildEditPatch.combine([original, incoming]) == BuildEditPatch(version_spec="1.21", normal_opening_time=None)


def test_name_and_visible_timings_preserve_omissions_and_allow_clears() -> None:
    build = DoorBuild(display_name="Original", visible_opening_time=7, visible_closing_time=9)
    BuildEditPatch(display_name="Renamed", visible_opening_time=None).apply(build)
    assert build.display_name == "Renamed"
    assert build.visible_opening_time is None
    assert build.visible_closing_time == 9
    BuildEditPatch(display_name=None).apply(build)
    assert build.display_name is None


def test_visible_timings_reject_non_door_builds() -> None:
    with pytest.raises(InvalidBuildError, match="Door fields"):
        BuildEditPatch(visible_closing_time=3).apply(UtilityBuild())


def test_taxonomy_replacements_remove_only_the_replaced_fields_proposals() -> None:
    build = DoorBuild(
        extra_info={
            "unknown_restrictions": {
                "component_restrictions": ["New component"],
                "animated_restrictions": ["New motion"],
            },
            "unknown_patterns": ["New pattern"],
        }
    )
    BuildEditPatch(component_restrictions=[]).apply(build)
    assert build.extra_info["unknown_restrictions"] == {"animated_restrictions": ["New motion"]}
    assert build.extra_info["unknown_patterns"] == ["New pattern"]
    BuildEditPatch(door_type=[]).apply(build)
    assert "unknown_patterns" not in build.extra_info
    assert build.extra_info["unknown_restrictions"] == {"animated_restrictions": ["New motion"]}


def test_extender_edits_normalize_preserve_and_clear_geometry() -> None:
    build = ExtenderBuild(orientation="Horizontal", extension_length=3)
    BuildEditPatch(extender_orientation="up").apply(build)
    assert build.orientation == "Up"
    assert build.extension_length == 3
    BuildEditPatch(extension_length=None).apply(build)
    assert build.extension_length is None


@pytest.mark.parametrize("length", [0, -1, 513])
def test_extender_edits_reject_invalid_lengths(length: int) -> None:
    with pytest.raises(InvalidBuildError, match="between 1 and 512"):
        BuildEditPatch(extension_length=length).apply(ExtenderBuild())


def test_extender_geometry_rejects_other_categories() -> None:
    with pytest.raises(InvalidBuildError, match="Extender fields"):
        BuildEditPatch(extension_length=3).apply(DoorBuild())
