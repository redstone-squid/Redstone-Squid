"""The generic build edit UI must stay aligned with the edit patch."""

from dataclasses import replace

import discord
import pytest

import squid_ui as sl
from squid.bot.submission.ui.views import EDIT_FIELDS, _edit_form
from squid.builds.domain import BuildCategory, DoorBuild, ExtenderBuild, UtilityBuild
from squid_ui_discord.modal import build_form_modal
from squid_ui_discord.testing import assert_within_limits


def test_every_edit_field_emits_a_typed_patch_fragment() -> None:
    """Each parser result reaches its declared domain mutation without a string lookup."""
    samples = {
        "dimensions": "1x2x3",
        "door_dimensions": "2x3",
        "version_spec": "1.21",
        "door_type": "Full lamp, Funnel",
        "door_orientation_type": "Trapdoor",
        "wiring_placement_restrictions": "Seamless, Full Flush",
        "animated_restrictions": "Symmetrical, Full Sync",
        "component_restrictions": "Observerless",
        "miscellaneous_restrictions": "Directional, Locational",
        "normal_closing_time": "5",
        "normal_opening_time": "7",
        "creators_ign": "Alice, Bob",
        "image_urls": "https://example.com/image.png",
        "video_urls": "https://example.com/video.mp4",
        "world_download_urls": "https://example.com/world.zip",
        "completion_time": "2026-08-30",
        "extra_user_info": "Reader notes",
        "server_ip": "play.example.com",
        "coordinates": "1 2 3",
        "command_to_get_to_build": "/warp door",
    }
    source = DoorBuild()
    for field in EDIT_FIELDS:
        bound = field.bind(source)
        bound.stage(samples[field.key])
        assert bound.validation_error is None

        target = DoorBuild()
        bound.to_patch().apply(target)

        assert field.reader(target) == bound.value, field.key


def test_edit_field_names_are_unique() -> None:
    attributes = [field.key for field in EDIT_FIELDS]
    assert len(attributes) == len(set(attributes))


def test_door_only_fields_are_offered_to_doors_alone() -> None:
    door_only = {field.key for field in EDIT_FIELDS if field.categories == frozenset({BuildCategory.DOOR})}
    assert door_only == {
        "door_dimensions",
        "door_orientation_type",
        "normal_opening_time",
        "normal_closing_time",
    }

    door = DoorBuild()
    assert {field.key for field in EDIT_FIELDS if field.applies_to(door)} >= door_only
    for build in (ExtenderBuild(), UtilityBuild()):
        offered = {field.key for field in EDIT_FIELDS if field.applies_to(build)}
        assert offered & door_only == set()
        # The shared fields stay available on every category.
        assert "dimensions" in offered
        assert "creators_ign" in offered


def test_every_edit_field_round_trips_through_its_declared_formatter_and_parser() -> None:
    build = DoorBuild()

    for field in EDIT_FIELDS:
        if not field.applies_to(build):
            continue
        bound = field.bind(build)
        assert field.parser(bound.current_text) == bound.value, field.key


def test_edit_form_metadata_comes_from_the_same_specs_as_parsing() -> None:
    items = tuple(field.bind(DoorBuild()) for field in EDIT_FIELDS if field.applies_to(DoorBuild()))

    for page in range(1, (len(items) + 4) // 5 + 1):
        expected = items[5 * (page - 1) : 5 * page]
        controls = [
            item
            for item in _edit_form(items, page).items
            if isinstance(item, sl.forms.TextField | sl.forms.TextAreaField)
        ]
        assert [control.key for control in controls] == [item.spec.key for item in expected]
        assert [control.label for control in controls] == [item.spec.label for item in expected]
        assert [control.placeholder for control in controls] == [item.spec.placeholder for item in expected]
        assert [control.maximum for control in controls] == [item.spec.maximum for item in expected]


def test_every_edit_form_page_fits_the_strict_discord_modal_boundary() -> None:
    async def submit(_interaction: discord.Interaction, _values: dict[str, object]) -> None:
        pass

    build = DoorBuild()
    items = tuple(field.bind(build) for field in EDIT_FIELDS if field.applies_to(build))
    for page in range(1, (len(items) + 4) // 5 + 1):
        modal = build_form_modal(_edit_form(items, page), on_submit=submit, strict=True)
        assert_within_limits(modal)


@pytest.mark.parametrize("field_key", ["image_urls", "video_urls", "world_download_urls"])
def test_edit_link_fields_name_every_invalid_url(field_key: str) -> None:
    field = next(field for field in EDIT_FIELDS if field.key == field_key)
    bound = field.bind(DoorBuild())

    bound.stage("https://valid.example/file, invalid, ftp://also-invalid")

    assert bound.modified is False
    assert bound.validation_error is not None
    assert "`invalid`, `ftp://also-invalid`" in bound.validation_error


def test_unexpected_edit_parser_failures_propagate() -> None:
    field = next(field for field in EDIT_FIELDS if field.key == "version_spec")

    def fail(_value: str) -> object:
        raise RuntimeError("parser defect")

    bound = replace(field, parser=fail).bind(DoorBuild())

    with pytest.raises(RuntimeError, match="parser defect"):
        bound.stage("changed")
