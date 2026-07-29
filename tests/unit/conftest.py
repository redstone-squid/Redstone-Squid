"""Fixtures used only by legacy unit tests."""

import pytest

from squid.builds.infrastructure.models import Restriction


@pytest.fixture
def sample_restriction_data() -> list[Restriction]:
    """Return representative persisted restrictions."""
    restrictions = [
        Restriction(name="No pistons", type="component", build_category="Door"),
        Restriction(name="No observers", type="component", build_category="Door"),
        Restriction(name="No redstone dust", type="component", build_category="Door"),
        Restriction(name="1-wide", type="wiring-placement", build_category="Door"),
        Restriction(name="2-wide", type="miscellaneous", build_category="Door"),
    ]
    for i, restriction in enumerate(restrictions):
        restriction.id = i + 1
    return restrictions
