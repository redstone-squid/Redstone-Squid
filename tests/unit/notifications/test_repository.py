"""Notification materialization matching contracts."""

from decimal import Decimal

from squid.notifications.infrastructure.repository import _exact_value


def test_exact_tag_matching_does_not_cross_boolean_and_numeric_types() -> None:
    assert _exact_value(Decimal("1"), 1) is True
    assert _exact_value(Decimal("1"), "1.0") is True
    assert _exact_value(Decimal("1"), expected=True) is False
    assert _exact_value(stored=True, expected=True) is True
    assert _exact_value(stored=True, expected=1) is False
    assert _exact_value("1", 1) is False
