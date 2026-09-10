"""Every control the planners convert must survive measurement as a top-level node.

`RoutedButton` regressed exactly here: measurement dropped it into the catch-all arm, so
`Realized` never carried one and the `case RoutedButton()` arms in both planners were
unreachable. Validation accepted a bare one, so the failure surfaced as
"must be normalized before measuring" from a caller that had skipped nothing.
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
    """A control the dialects know how to convert reaches them instead of raising."""
    solved = measure([control])  # type: ignore[arg-type]

    assert solved.children, f"{type(control).__name__} was dropped during measurement"


def test_routed_button_is_carried_through_rather_than_replaced() -> None:
    """Measurement passes the control through, so the planner arm for it is reachable."""
    solved = measure([RoutedButton(label="x", route_id="route")])

    assert [type(child).__name__ for child in solved.children] == ["RoutedButton"]
