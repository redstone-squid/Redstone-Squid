import pytest

from squid.records.domain.formatting import DoorCategory, ExtenderCategory, RulesTitleFormatter
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

    assert category.title == "flush seamless full-sync 5x5 iris funnel trapdoor"
    assert category.subtitle == "no slimes observerless directional"


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

    assert category.title == "seamless upward 3 glass piston extender"
    assert category.subtitle == "slimeless"


def test_formats_record_class_before_category() -> None:
    formatter = RulesTitleFormatter()
    category = formatter.format_door(
        DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size="2x2",
            types=("regular",),
            orientation="door",
        )
    )

    record = formatter.format_record(RecordClass.SMALLEST, category)

    assert record.title == "smallest 2x2 regular door"
    assert record.subtitle is None


def test_extender_category_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError, match="positive"):
        ExtenderCategory(wiring_restrictions=(), orientation="upward", length=0, types=("regular",))
