"""The generic build edit UI must stay aligned with the edit patch."""

from dataclasses import replace
from typing import Never

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.views import EDIT_FIELDS, BuildEditScreen, _edit_form
from squid.builds.application import BuildService
from squid.builds.domain import BuildCategory
from squid.builds.models import DoorBuild, ExtenderBuild, UtilityBuild
from squid_ui.testing import submit_event
from squid_ui.text import source_text
from squid_ui_discord.modal import build_form_modal
from squid_ui_discord.testing import assert_within_limits, commit_render, interaction_harness


def test_every_edit_field_emits_a_typed_patch_fragment() -> None:
    """Each parser result reaches its declared domain mutation without a string lookup."""
    samples = {
        "extender_orientation": "Up",
        "extension_length": "5",
        "display_name": "My build",
        "visible_opening_time": "3gt",
        "visible_closing_time": "4gt",
        "dimensions": "1x2x3",
        "door_dimensions": "2x3",
        "version_spec": "1.21",
        "door_type": "Full lamp, Funnel",
        "door_orientation_type": "Trapdoor",
        "wiring_placement_restrictions": "Seamless, Full Flush",
        "animated_restrictions": "Symmetrical, Full Sync",
        "component_restrictions": "Observerless",
        "miscellaneous_restrictions": "Directional, Locational",
        "normal_closing_time": "5gt",
        "normal_opening_time": "7gt",
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
    assert len(EDIT_FIELDS) == len(samples)
    assert {field.key for field in EDIT_FIELDS} == samples.keys()
    for field in EDIT_FIELDS:
        source = ExtenderBuild() if field.categories == frozenset({BuildCategory.EXTENDER}) else DoorBuild()
        bound = field.bind(source)
        assert field.parser(bound.current_text) == bound.value, field.key
        bound.stage(samples[field.key])
        assert bound.validation_error is None

        target = type(source)()
        bound.to_patch().apply(target)

        assert field.reader(target) == bound.value, field.key


def test_door_only_fields_are_offered_to_doors_alone() -> None:
    door_only = {field.key for field in EDIT_FIELDS if field.categories == frozenset({BuildCategory.DOOR})}
    assert door_only == {
        "door_dimensions",
        "door_orientation_type",
        "normal_opening_time",
        "normal_closing_time",
        "visible_opening_time",
        "visible_closing_time",
    }

    door = DoorBuild()
    assert {field.key for field in EDIT_FIELDS if field.applies_to(door)} >= door_only
    for build in (ExtenderBuild(), UtilityBuild()):
        offered = {field.key for field in EDIT_FIELDS if field.applies_to(build)}
        assert offered & door_only == set()
        # The shared fields stay available on every category.
        assert "dimensions" in offered
        assert "creators_ign" in offered


def test_blank_values_preserve_optional_text_numbers_and_list_semantics() -> None:
    fields = {field.key: field for field in EDIT_FIELDS}
    assert fields["version_spec"].parser("") is None
    assert fields["version_spec"].parser(" ") == " "
    assert fields["normal_opening_time"].parser("") is None
    assert fields["normal_opening_time"].parser("0gt") == 0
    assert fields["normal_opening_time"].parser(" ") is None
    with pytest.raises(ValueError, match="Add a unit"):
        fields["normal_opening_time"].parser("7")
    assert fields["creators_ign"].parser(" Alice, , Bob, ") == ["Alice", "Bob"]
    assert fields["creators_ign"].parser(" , ") == []


async def test_empty_optional_modal_fields_clear_staged_text_and_numbers() -> None:
    class Builds(BuildService):
        def __init__(self) -> None:
            pass

    async def authorize() -> bool:
        return True

    async def unexpected_write(*args: object) -> Never:
        raise AssertionError("Staging edits must not publish a changed build")

    build = DoorBuild()
    fields = {field.key: field for field in EDIT_FIELDS}
    version = fields["version_spec"].bind(build)
    timing = fields["normal_opening_time"].bind(build)
    version.stage("1.21")
    timing.stage("7gt")
    items = (version, timing)
    screen = BuildEditScreen(
        build,
        Builds(),
        items,
        authorize=authorize,
        render_build=unexpected_write,
        refresh_posts=unexpected_write,
    )
    parsed = await _edit_form(items, 1).evaluate({"version_spec": "", "normal_opening_time": ""})
    assert parsed.errors == ()
    assert parsed.values == {"version_spec": None, "normal_opening_time": None}

    with sl.runtime.transaction():
        await screen._edited(tuple(parsed.values), submit_event(parsed.values))

    assert all(item.value is None for item in screen.items)
    assert screen.validation_error is None
    assert (await screen.staged_history.undo()).applied
    assert tuple(item.value for item in screen.items) == (version.value, timing.value)
    assert (await screen.staged_history.redo()).applied
    assert all(item.value is None for item in screen.items)


def test_edit_form_pages_preserve_field_metadata_and_fit_discord_limits() -> None:
    async def submit(_interaction: discord.Interaction, _values: dict[str, object]) -> None:
        pass

    items = tuple(field.bind(DoorBuild()) for field in EDIT_FIELDS if field.applies_to(DoorBuild()))

    for page in range(1, (len(items) + 4) // 5 + 1):
        expected = items[5 * (page - 1) : 5 * page]
        controls = [
            item
            for item in _edit_form(items, page).items
            if isinstance(item, sl.forms.TextField | sl.forms.TextAreaField)
        ]
        assert [control.key for control in controls] == [item.spec.key for item in expected]
        assert [source_text(control.label or "") for control in controls] == [
            source_text(item.spec.label) for item in expected
        ]
        assert [source_text(control.placeholder or "") for control in controls] == [
            source_text(item.spec.placeholder) for item in expected
        ]
        assert [control.maximum for control in controls] == [item.spec.maximum for item in expected]
        assert [control.required for control in controls] == [item.spec.required for item in expected]
        modal = build_form_modal(_edit_form(items, page), on_submit=submit, strict=True)
        assert_within_limits(modal)


async def test_invalid_edit_urls_reopen_the_modal_with_every_attempted_value() -> None:
    class Builds(BuildService):
        def __init__(self) -> None:
            pass

    async def authorize() -> bool:
        return True

    async def unexpected_write(*args: object) -> Never:
        raise AssertionError("Invalid modal input must not render or publish a changed build")

    build = DoorBuild()
    items = tuple(
        field.bind(build) for field in EDIT_FIELDS if field.key in {"image_urls", "video_urls", "world_download_urls"}
    )
    screen = BuildEditScreen(
        build,
        Builds(),
        items,
        authorize=authorize,
        render_build=unexpected_write,
        refresh_posts=unexpected_write,
    )
    await screen.projection.reload()
    root = sd.MessageRoot(screen, access=sd.Owner(7), timeout=None)
    commit_render(root)

    opened = interaction_harness(7)
    await root.dispatch("edit_files", opened)
    modal = opened.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    attempted = {
        "image_urls": "https://valid.example/image.png, invalid-image",
        "video_urls": "ftp://invalid-video, relative-video",
        "world_download_urls": "https://valid.example/world.zip",
    }
    # Fill the payload slots discord.py normally populates before calling on_submit.
    for child in modal.children:
        assert isinstance(child, discord.ui.Label)
        component = child.component
        assert isinstance(component, discord.ui.TextInput)
        component._value = attempted[component.custom_id]  # pyrefly: ignore[missing-attribute]

    submitted = interaction_harness(7)
    await modal.on_submit(submitted)

    assert all(not item.modified and item.current_text == "" for item in items)
    retry_view = submitted.response.send_message.await_args.kwargs["view"]
    retry = next(item for item in retry_view.walk_children() if isinstance(item, discord.ui.Button))
    reopened = interaction_harness(7)
    await retry.callback(reopened)
    retried = reopened.response.send_modal.await_args.args[0]
    assert isinstance(retried, discord.ui.Modal)
    defaults = {
        component.custom_id: component.default
        for child in retried.children
        if isinstance(child, discord.ui.Label) and isinstance((component := child.component), discord.ui.TextInput)
    }
    assert defaults == attempted


def test_edit_link_fields_name_every_invalid_url() -> None:
    for field in EDIT_FIELDS:
        if field.key not in {"image_urls", "video_urls", "world_download_urls"}:
            continue
        bound = field.bind(DoorBuild())
        bound.stage("https://valid.example/file, invalid, ftp://also-invalid")

        assert bound.modified is False
        assert bound.validation_error is not None
        assert "`invalid`, `ftp://also-invalid`" in source_text(bound.validation_error)


def test_unexpected_edit_parser_failures_propagate() -> None:
    field = next(field for field in EDIT_FIELDS if field.key == "version_spec")

    def fail(_value: str) -> object:
        raise RuntimeError("parser defect")

    bound = replace(field, parser=fail).bind(DoorBuild())

    with pytest.raises(RuntimeError, match="parser defect"):
        bound.stage("changed")


@pytest.mark.parametrize("section", ["restrictions", "files"])
async def test_optional_lists_allow_empty_modal_values_and_clear_existing_entries(section: str) -> None:
    keys = (
        {
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
        }
        if section == "restrictions"
        else {"image_urls", "video_urls", "world_download_urls"}
    )
    build = DoorBuild()
    items = tuple(field.bind(build) for field in EDIT_FIELDS if field.key in keys)
    for item in items:
        item.stage("https://example.com/file" if section == "files" else "Example")
        item.to_patch().apply(build)
    form = _edit_form(items, 1)
    assert all(not control.required for control in form.items if isinstance(control, sl.forms.TextField))
    result = await form.evaluate({key: "" for key in keys})
    assert result.errors == ()
    for item in items:
        item.stage("")
        item.to_patch().apply(build)
        assert item.spec.reader(build) == []


def test_edit_fields_include_retained_taxonomy_proposals() -> None:
    build = DoorBuild(
        component_restrictions=["Observerless"],
        extra_info={
            "unknown_restrictions": {"component_restrictions": ["New component"]},
            "unknown_patterns": ["New pattern"],
        },
    )
    fields = {field.key: field.bind(build) for field in EDIT_FIELDS if field.applies_to(build)}
    assert fields["component_restrictions"].value == ["Observerless", "New component"]
    assert fields["door_type"].value == ["New pattern"]
    fields["component_restrictions"].stage("")
    fields["component_restrictions"].to_patch().apply(build)
    assert "unknown_restrictions" not in build.extra_info
    assert build.extra_info["unknown_patterns"] == ["New pattern"]
