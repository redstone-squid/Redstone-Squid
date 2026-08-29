"""The generic build edit UI must stay aligned with the edit patch."""

from squid.bot.submission.ui.views import EDIT_FIELDS
from squid.builds.application import BuildEditPatch
from squid.builds.domain import BuildCategory, DoorBuild, ExtenderBuild, UtilityBuild


def test_every_edit_field_names_a_patch_field() -> None:
    """A field the patch cannot carry would only fail when the user submits."""
    unknown = {field.patch_key for field in EDIT_FIELDS} - set(BuildEditPatch.__dataclass_fields__)
    assert unknown == set()


def test_edit_field_names_are_unique() -> None:
    attributes = [field.patch_key for field in EDIT_FIELDS]
    assert len(attributes) == len(set(attributes))


def test_door_only_fields_are_offered_to_doors_alone() -> None:
    door_only = {field.patch_key for field in EDIT_FIELDS if field.categories == frozenset({BuildCategory.DOOR})}
    assert door_only == {
        "door_dimensions",
        "door_orientation_type",
        "normal_opening_time",
        "normal_closing_time",
    }

    door = DoorBuild()
    assert {field.patch_key for field in EDIT_FIELDS if field.applies_to(door)} >= door_only
    for build in (ExtenderBuild(), UtilityBuild()):
        offered = {field.patch_key for field in EDIT_FIELDS if field.applies_to(build)}
        assert offered & door_only == set()
        # The shared fields stay available on every category.
        assert "dimensions" in offered
        assert "creators_ign" in offered
