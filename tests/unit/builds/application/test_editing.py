"""Typed build-edit patch composition."""

from squid.builds.application import BuildEditPatch


def test_combine_preserves_every_typed_patch_field() -> None:
    patch = BuildEditPatch(
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
        extra_info={"source": "test"},
        ai_generated=False,
    )

    assert BuildEditPatch.combine([patch]) == patch


def test_combine_prefers_the_latest_fragment() -> None:
    combined = BuildEditPatch.combine(
        [BuildEditPatch(version_spec="1.20"), BuildEditPatch(image_urls=["old"]), BuildEditPatch(version_spec="1.21")]
    )

    assert combined == BuildEditPatch(version_spec="1.21", image_urls=["old"])
