"""Typed build-edit patch composition."""

from copy import deepcopy

import pytest

from squid.builds.application import BuildEditPatch
from squid.builds.errors import InvalidBuildError
from squid.builds.models import DoorBuild, UtilityBuild
from squid.core.sentinel import UNSET


def test_combine_preserves_every_typed_patch_field() -> None:
    patch = BuildEditPatch(
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
