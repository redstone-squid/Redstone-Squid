"""Unresolved taxonomy names keep their storage shape and stay unique by normalized name."""

import pytest

from squid.builds.domain import Info, UnresolvedTaxonomy
from squid.core.errors import DataIntegrityError


def test_round_trip_preserves_storage_shape_and_unrelated_keys() -> None:
    info: Info = {
        "unknown_restrictions": {"component_restrictions": ["No slime"], "miscellaneous_restrictions": ["1.5 s"]},
        "unknown_patterns": ["Spiral"],
        "server_info": {"server_ip": "play.example"},
    }
    original = dict(info)

    UnresolvedTaxonomy.read(info).write(info)

    assert info == original


def test_adding_keeps_the_first_spelling_and_field_of_each_name() -> None:
    names = UnresolvedTaxonomy().add(
        restrictions=[("component_restrictions", "No  Slime"), ("wiring_placement_restrictions", "no slime")],
        patterns=["Spiral", "spiral", "  "],
    )

    assert names.restrictions == (("component_restrictions", "No  Slime"),)
    assert names.patterns == ("Spiral",)


def test_retain_keeps_names_still_unknown_by_normalized_form() -> None:
    names = UnresolvedTaxonomy(
        restrictions=(("component_restrictions", "No Slime"), ("animated_restrictions", "Smooth")),
        patterns=("Spiral", "Regular"),
    )

    kept = names.retain(restrictions=frozenset({"no slime"}), patterns=frozenset({"spiral"}))

    assert kept == UnresolvedTaxonomy(restrictions=(("component_restrictions", "No Slime"),), patterns=("Spiral",))


def test_writing_nothing_removes_both_keys() -> None:
    info: Info = {"unknown_restrictions": {"component_restrictions": []}, "unknown_patterns": [], "user_note": 1}  # type: ignore[typeddict-unknown-key]

    UnresolvedTaxonomy().write(info)

    assert info == {"user_note": 1}


@pytest.mark.parametrize(
    "info",
    [
        {"unknown_restrictions": ["No slime"]},
        {"unknown_restrictions": {"door_restrictions": ["No slime"]}},
        {"unknown_restrictions": {"component_restrictions": "No slime"}},
        {"unknown_patterns": [1]},
    ],
)
def test_malformed_storage_is_a_data_integrity_error(info: Info) -> None:
    with pytest.raises(DataIntegrityError):
        UnresolvedTaxonomy.read(info)
