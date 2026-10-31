"""Sanity pins for the limits table."""

import dataclasses

from squid_layouts.discord import DEFAULT_LIMITS as LIMITS


def test_limits_are_positive():
    for field in dataclasses.fields(LIMITS):
        assert getattr(LIMITS, field.name) > 0, field.name


def test_headline_budgets():
    # The two budgets the whole engine design hangs off; moving them is a deliberate act.
    assert LIMITS.total_text == 4000
    assert LIMITS.total_components == 40
