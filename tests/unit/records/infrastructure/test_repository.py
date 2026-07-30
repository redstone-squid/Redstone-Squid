from squid.records.application.models import CategoryIdentity
from squid.records.domain import BuildKind
from squid.records.infrastructure.repository import _parse_category_key


def test_requested_category_key_round_trips() -> None:
    category = CategoryIdentity(
        kind=BuildKind.EXTENDER,
        base_key="extender|upward|3|t[20]",
        restriction_ids=(2, 4, 9),
    )

    assert _parse_category_key(category.key) == category


def test_requested_category_parser_ignores_malformed_legacy_key() -> None:
    assert _parse_category_key("not-a-record-category") is None
