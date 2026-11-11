"""Canonical tag command values independent of persistence and transport."""

from decimal import Decimal

import pytest

from squid.core.errors import ValidationError
from squid.tags.application.edits import ProposalValues
from squid.tags.application.services import coerce_assignment_value, require_showcase
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition


def test_proposal_normalizes_public_names_and_generates_an_opaque_key() -> None:
    values = ProposalValues(
        display_name="  Closing   Showcase ", value_type=TagValueType.NUMERIC, query_name="CLOSING_SHOWCASE"
    )
    assert values.display_name == "Closing Showcase"
    assert values.query_name == "closing_showcase"
    assert values.stable_key.startswith("user_")
    assert len(values.stable_key) == 37


@pytest.mark.parametrize("query", ["not-valid!", "123", "x" * 65])
def test_invalid_public_query_name_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="query names"):
        ProposalValues(display_name="Compact", value_type=TagValueType.NONE, query_name=query)


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (TagValueType.NONE, None, None),
        (TagValueType.NUMERIC, "0.4", Decimal("0.4")),
        (TagValueType.TEXT, "  hello  ", "hello"),
        (TagValueType.BOOLEAN, "YES", True),
    ],
)
def test_approved_showcase_values_are_coerced_to_the_declared_type(
    kind: TagValueType, raw: str | None, expected: object
) -> None:
    definition = TagDefinition(
        stable_key="user_example",
        display_name="Showcase",
        authority=TagAuthority.USER,
        semantic_kind=TagSemanticKind.SHOWCASE,
        value_type=kind,
        moderation_status=TagModerationStatus.APPROVED,
    )
    assert coerce_assignment_value(require_showcase(definition), raw) == expected
    definition.withdrawn = True
    with pytest.raises(ValidationError):
        require_showcase(definition)
