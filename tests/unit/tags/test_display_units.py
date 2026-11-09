"""Presentation converts units without changing values used by record rules."""

from dataclasses import replace
from decimal import Decimal

import pytest

from squid.records.infrastructure.repository import _restriction_facets
from squid.search.infrastructure.projection import _render_tag_assignment
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition, UnitDefinition, convert_display_value


def test_display_conversion_and_category_thresholds() -> None:
    tick = UnitDefinition(key="tick", dimension="time", symbol="gt", scale_to_base=Decimal("0.05"))
    second = UnitDefinition(key="second", dimension="time", symbol="s", scale_to_base=Decimal(1))
    definition = TagDefinition(
        id=1,
        stable_key="delay",
        display_name="Delay",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=TagSemanticKind.RESTRICTION,
        value_type=TagValueType.NUMERIC,
        moderation_status=TagModerationStatus.APPROVED,
        canonical_unit="tick",
        default_display_unit="second",
        render_template="{name}: {value} {unit}",
    )
    definition.canonical_unit_definition = tick
    definition.default_display_unit_definition = second
    assignment = TagAssignment(definition, Decimal(20))
    assignment.tag_id = 1
    assert assignment.presentation_value == Decimal(1)
    assert _render_tag_assignment(assignment) == "Delay: 1.00 second"
    facet = _restriction_facets([assignment])[0]
    assert facet.assigned_value == Decimal(20)
    assert replace(facet, category_value=Decimal(40)).category_name == "Delay: 2 second"
    assignment.display_unit_definition = tick
    assert assignment.presentation_value == Decimal(20)
    assert assignment.value == Decimal(20)


def test_conversion_rejects_incompatible_units() -> None:
    length = UnitDefinition(key="block", dimension="length", symbol="b", scale_to_base=Decimal(1))
    time = UnitDefinition(key="second", dimension="time", symbol="s", scale_to_base=Decimal(1))
    with pytest.raises(ValueError, match="same dimension"):
        convert_display_value(Decimal(1), length, time)
    assert convert_display_value(None, None, None) is None
    assert convert_display_value(value=True, canonical=None, display=None) is True
