import pytest

from squid.catalogue.domain import (
    DoorCategory,
    ExtenderCategory,
    RulesTitleFormatter,
    TitleDiagnosticCode,
    TitleSection,
    TrapdoorPlacement,
)
from squid.records.domain.models import RecordClass


def test_formats_door_title_in_rules_order() -> None:
    formatter = RulesTitleFormatter()

    category = formatter.format_door(
        DoorCategory(
            wiring_restrictions=("flush", "seamless"),
            animated_restrictions=("full-sync",),
            size="5x5",
            types=("iris", "funnel"),
            orientation="trapdoor",
            component_restrictions=("no slimes", "observerless"),
            miscellaneous_restrictions=("directional",),
        )
    )

    assert category.title == "seamless flush full-sync 5x5 iris funnel trapdoor"
    assert category.subtitle == "observerless no slimes directional"
    assert [token.section for token in category.title_tokens] == [
        TitleSection.WIRING,
        TitleSection.WIRING,
        TitleSection.ANIMATED,
        TitleSection.SIZE,
        TitleSection.TYPE,
        TitleSection.TYPE,
        TitleSection.ORIENTATION,
    ]


def test_formats_extender_title_in_rules_order() -> None:
    formatter = RulesTitleFormatter()

    category = formatter.format_extender(
        ExtenderCategory(
            wiring_restrictions=("seamless",),
            orientation="upward",
            length=3,
            types=("glass",),
            component_restrictions=("slimeless",),
        )
    )

    assert category.title == "seamless upward 3 glass Piston Extender"
    assert category.subtitle == "slimeless"


def test_formats_record_class_before_category() -> None:
    formatter = RulesTitleFormatter()
    category = formatter.format_door(
        DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size="2x2",
            types=("Regular",),
            orientation="door",
        )
    )

    record = formatter.format_record(RecordClass.SMALLEST, category)

    assert record.title == "Smallest 2x2 door"
    assert record.subtitle is None


def test_extender_category_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError, match="positive"):
        ExtenderCategory(wiring_restrictions=(), orientation="upward", length=0, types=("regular",))


def test_preserves_display_case_deduplicates_and_orders_unknown_terms_last() -> None:
    category = RulesTitleFormatter().format_door(
        DoorCategory(
            wiring_restrictions=("Unlisted Zebra", "FLUSH", "unlisted alpha", "flush"),
            animated_restrictions=(),
            size="3x3",
            types=("Regular", "Mystery", "IRIS"),
            orientation="DOOR",
        )
    )

    assert category.title == "FLUSH unlisted alpha Unlisted Zebra 3x3 IRIS Mystery DOOR"
    assert category.title_tokens[0].source_value == "FLUSH"
    assert category.title_tokens[1].recognized is False
    assert {diagnostic.code for diagnostic in category.diagnostics} == {
        TitleDiagnosticCode.DUPLICATE_TERM,
        TitleDiagnosticCode.UNKNOWN_TERM,
    }


def test_prefers_non_alias_term_without_changing_stored_spelling() -> None:
    category = RulesTitleFormatter().format_door(
        DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size="4x4",
            types=("VAULT", "Dual Funnel"),
            orientation="Door",
        )
    )

    assert category.title == "4x4 Dual Funnel Door"
    diagnostic = next(item for item in category.diagnostics if item.code is TitleDiagnosticCode.DUPLICATE_ALIAS)
    assert diagnostic.as_dict()["terms"] == ["VAULT", "Dual Funnel"]


def test_explicit_trapdoor_placement_rewrites_orientation_and_suppresses_restriction() -> None:
    category = RulesTitleFormatter().format_door(
        DoorCategory(
            wiring_restrictions=("Full Trapdoor", "Flush"),
            animated_restrictions=(),
            size="2x2",
            types=("Regular",),
            orientation="Skydoor",
            trapdoor_placement=TrapdoorPlacement.FLOOR,
        )
    )

    assert category.title == "Flush 2x2 Floor Trapdoor"
    assert not category.diagnostics


def test_ambiguous_trapdoor_is_retained_with_diagnostic() -> None:
    category = RulesTitleFormatter().format_door(
        DoorCategory(
            wiring_restrictions=("Trapdoor",),
            animated_restrictions=(),
            size="2x2",
            types=(),
            orientation="Skydoor",
        )
    )

    assert category.title == "Trapdoor 2x2 Skydoor"
    assert category.diagnostics[-1].code is TitleDiagnosticCode.AMBIGUOUS_TRAPDOOR


def test_unknown_subtitle_terms_keep_section_provenance() -> None:
    category = RulesTitleFormatter().format_extender(
        ExtenderCategory(
            wiring_restrictions=(),
            orientation="Horizontal",
            length=2,
            types=(),
            component_restrictions=("No Observers", "Custom Component"),
            miscellaneous_restrictions=("Up-To-Date",),
        )
    )

    assert category.subtitle == "No Observers Custom Component Up-To-Date"
    assert category.subtitle_tokens[1] == category.subtitle_tokens[1].__class__(
        value="Custom Component",
        section=TitleSection.COMPONENT,
        recognized=False,
        source_value="Custom Component",
    )


def test_recognizes_rules_animated_and_dynamic_wiring_terms() -> None:
    category = RulesTitleFormatter().format_door(
        DoorCategory(
            wiring_restrictions=("Weatherproof", "3 Wide", "Full Tileable"),
            animated_restrictions=("Scissor", "Clean", "Super Sync"),
            size="3 Wide",
            types=(),
            orientation="Trapdoor",
        )
    )

    assert all(token.recognized for token in category.title_tokens)
    assert category.title == "Full Tileable Weatherproof 3 Wide Super Sync Clean Scissor 3 Wide Trapdoor"
