"""Saved builds round-trip through edit-form answers without changing what the user did not touch."""

import pytest

from squid.builds.domain import Status, UnresolvedTaxonomy
from squid.builds.models import Build, DoorBuild, ExtenderBuild, UtilityBuild
from squid.core.sentinel import UNSET
from squid.submissions.application.build_answers import answers_from_build, patch_from_answers
from squid.submissions.application.forms import build_edit_manifest
from squid.submissions.domain import ChoiceOption
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition

OPTIONS = {
    "approved_restrictions": (ChoiceOption("seamless", "Seamless"), ChoiceOption("flush", "Flush")),
    "approved_patterns": (ChoiceOption("piston-door", "Piston Door"),),
}


def _door() -> DoorBuild:
    door = DoorBuild(id=1, submission_status=Status.CONFIRMED, submitter_account_id=1, orientation="Door")
    door.door_width, door.door_height = 2, 2
    door.wiring_placement_restrictions = ["Seamless"]
    door.miscellaneous_restrictions = ["Hand-wired"]
    door.patterns = ["Piston Door"]
    return door


def _extender() -> ExtenderBuild:
    extender = ExtenderBuild(id=2, submission_status=Status.CONFIRMED, submitter_account_id=1)
    extender.orientation, extender.extension_length, extender.retraction_time = "Upward", 4, 6
    return extender


def _utility() -> UtilityBuild:
    utility = UtilityBuild(id=3, submission_status=Status.CONFIRMED, submitter_account_id=1, versions=["Java 1.21"])
    compact = TagDefinition(
        id=1,
        stable_key="compact",
        display_name="Compact",
        authority=TagAuthority.USER,
        semantic_kind=TagSemanticKind.SHOWCASE,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
    )
    utility.tags = [TagAssignment(compact)]
    utility.extra_info = {"server_info": {"server_ip": "play.example.com"}}
    return utility


@pytest.mark.parametrize("build", [_door(), _extender(), _utility()], ids=["door", "extender", "utility"])
def test_an_untouched_build_produces_an_empty_patch(build: Build) -> None:
    before = answers_from_build(build, OPTIONS)

    patch = patch_from_answers(before, before.answers, before.unresolved, OPTIONS)

    assert patch.attributes() == {}


@pytest.mark.parametrize("build", [_door(), _extender(), _utility()], ids=["door", "extender", "utility"])
def test_every_answer_is_a_field_of_the_edit_form(build: Build) -> None:
    fields = {field.id for field in build_edit_manifest().fields_for(build.category.value.lower())}

    assert answers_from_build(build, OPTIONS).answers.keys() <= fields


def test_versions_and_showcase_tags_are_replaced_as_lists() -> None:
    before = answers_from_build(_utility(), OPTIONS)

    assert (before.answers["versions"], before.answers["showcase_tags"]) == (["Java 1.21"], ["compact"])
    patch = patch_from_answers(
        before, {**before.answers, "versions": [], "showcase_tags": ["compact", "tileable"]}, before.unresolved, OPTIONS
    )
    assert patch.attributes() == {"versions": [], "showcase_tags": ["compact", "tileable"]}


def test_names_without_a_catalogue_entry_are_kept_aside_in_their_list() -> None:
    before = answers_from_build(_door(), OPTIONS)

    assert before.answers["restrictions"] == ["seamless"]
    assert before.unresolved.restrictions == (("miscellaneous_restrictions", "Hand-wired"),)

    patch = patch_from_answers(before, {**before.answers, "restrictions": ["flush"]}, before.unresolved, OPTIONS)

    # The edit command re-files resolved names by tag type, so they all travel in one list.
    assert patch.miscellaneous_restrictions == ["Flush", "Hand-wired"]
    assert patch.wiring_placement_restrictions == []


def test_removing_an_unmatched_name_is_a_change() -> None:
    before = answers_from_build(_door(), OPTIONS)

    patch = patch_from_answers(before, before.answers, UnresolvedTaxonomy(), OPTIONS)

    assert patch.miscellaneous_restrictions == ["Seamless"]
    assert patch.door_type is UNSET


def test_one_changed_dimension_sends_the_whole_size() -> None:
    before = answers_from_build(_door(), OPTIONS)

    patch = patch_from_answers(before, {**before.answers, "opening_height": 3}, before.unresolved, OPTIONS)

    assert patch.door_dimensions == (2, 3, None)
    assert patch.dimensions is UNSET


def test_door_types_use_the_stored_spelling() -> None:
    before = answers_from_build(_door(), OPTIONS)

    assert before.answers["door_orientation"] == "door"
    patch = patch_from_answers(before, {**before.answers, "door_orientation": "skydoor"}, before.unresolved, OPTIONS)

    assert patch.door_orientation_type == "Skydoor"


def test_an_extender_keeps_its_direction_unless_changed() -> None:
    before = answers_from_build(_extender(), OPTIONS)

    assert before.answers["movement_orientation"] == "vertical_up"
    assert patch_from_answers(before, before.answers, before.unresolved, OPTIONS).extender_orientation is UNSET

    changed = patch_from_answers(
        before, {**before.answers, "movement_orientation": "horizontal"}, before.unresolved, OPTIONS
    )
    assert changed.extender_orientation == "Horizontal"


def test_extender_timings_are_answers() -> None:
    extender = ExtenderBuild(id=2, submission_status=Status.CONFIRMED, submitter_account_id=1, extension_time=8)
    before = answers_from_build(extender, OPTIONS)

    assert (before.answers["extension_time"], before.answers["retraction_time"]) == (8, None)
    patch = patch_from_answers(before, {**before.answers, "retraction_time": 6}, before.unresolved, OPTIONS)
    assert patch.attributes() == {"retraction_time": 6}


def test_clearing_a_text_field_clears_it() -> None:
    door = _door()
    door.display_name = "Fast door"
    before = answers_from_build(door, OPTIONS)

    patch = patch_from_answers(before, {**before.answers, "display_name": "  "}, before.unresolved, OPTIONS)

    assert patch.display_name is None
