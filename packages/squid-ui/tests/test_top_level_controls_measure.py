"""Top-level primitive controls must reach planners through measurement.

These fixtures are already normalized. A missing measurement branch must not reject
them as unnormalized input or silently drop them before target conversion.
"""

import pytest

from squid_ui.planning import measure
from squid_ui.primitives.nodes import LinkButton, PremiumButton, RoutedButton

CONTROLS = (
    pytest.param(LinkButton(label="x", url="https://example.test"), id="link"),
    pytest.param(RoutedButton(label="x", route_id="route"), id="routed"),
    pytest.param(PremiumButton(sku_id=1), id="premium"),
)


@pytest.mark.parametrize("control", CONTROLS)
def test_a_bare_control_measures(control: object) -> None:
    solved = measure([control])  # type: ignore[arg-type]

    assert solved.children, f"{type(control).__name__} was dropped during measurement"


def test_routed_button_is_carried_through_rather_than_replaced() -> None:
    solved = measure([RoutedButton(label="x", route_id="route")])

    assert [type(child).__name__ for child in solved.children] == ["RoutedButton"]
