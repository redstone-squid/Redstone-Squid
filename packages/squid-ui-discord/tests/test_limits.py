"""Sanity pins for the limits table."""

from squid_ui_discord import V2_LIMITS as LIMITS


def test_limits_are_positive():
    for name, value in LIMITS.digest():
        assert value is None or value > 0, name


def test_headline_budgets():
    # The two budgets the whole engine design hangs off; moving them is a deliberate act.
    assert LIMITS.total_text == 4000
    assert LIMITS.total_components == 40
