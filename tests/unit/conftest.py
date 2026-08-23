"""Fixtures shared by unit tests."""

import pytest

from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.infrastructure.models import TagDefinition


@pytest.fixture
def sample_restriction_data() -> list[TagDefinition]:
    """Return representative persisted restrictions."""
    values = [
        ("No pistons", "component"),
        ("No observers", "component"),
        ("No redstone dust", "component"),
        ("1-wide", "wiring-placement"),
        ("2-wide", "miscellaneous"),
    ]
    restrictions = [
        TagDefinition(
            stable_key=f"test_restriction_{index}",
            display_name=name,
            normalized_name=name.casefold(),
            authority=TagAuthority.OFFICIAL,
            semantic_kind=TagSemanticKind.RESTRICTION,
            restriction_type=restriction_type,
            value_type=TagValueType.NONE,
            moderation_status=TagModerationStatus.APPROVED,
        )
        for index, (name, restriction_type) in enumerate(values, start=1)
    ]
    for i, restriction in enumerate(restrictions):
        restriction.id = i + 1
    return restrictions
