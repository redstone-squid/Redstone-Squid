"""Saved builds round-trip through edit-form answers without changing what the user did not touch."""

from squid.builds.domain import Status, UnresolvedTaxonomy
from squid.builds.models import DoorBuild, ExtenderBuild
from squid.core.sentinel import UNSET
from squid.submissions.application.build_answers import answers_from_build, patch_from_answers
from squid.submissions.domain import ChoiceOption

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


def test_an_untouched_build_produces_an_empty_patch() -> None:
    before = answers_from_build(_door(), OPTIONS)

    patch = patch_from_answers(before, before.answers, before.unresolved, OPTIONS)

    assert patch.attributes() == {}


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


def test_an_extender_stored_by_a_submission_keeps_its_direction_unless_changed() -> None:
    extender = ExtenderBuild(id=2, submission_status=Status.CONFIRMED, submitter_account_id=1)
    extender.orientation, extender.extension_length = "Upward", 4
    before = answers_from_build(extender, OPTIONS)

    assert before.answers["movement_orientation"] == "vertical_up"
    assert patch_from_answers(before, before.answers, before.unresolved, OPTIONS).extender_orientation is UNSET

    changed = patch_from_answers(
        before, {**before.answers, "movement_orientation": "horizontal"}, before.unresolved, OPTIONS
    )
    assert changed.extender_orientation == "Horizontal"


def test_clearing_a_text_field_clears_it() -> None:
    door = _door()
    door.display_name = "Fast door"
    before = answers_from_build(door, OPTIONS)

    patch = patch_from_answers(before, {**before.answers, "display_name": "  "}, before.unresolved, OPTIONS)

    assert patch.display_name is None
