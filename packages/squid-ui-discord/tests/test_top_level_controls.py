"""Bare primitive controls survive public planning with their routing intact."""

import pytest

from squid_ui import scene
from squid_ui.ext.discord import DISCORD_V2_DPY27
from squid_ui.planning import plan
from squid_ui.primitives.nodes import LinkButton, PremiumButton, RoutedButton


@pytest.mark.parametrize(
    "control",
    [
        LinkButton(label="x", url="https://example.test"),
        RoutedButton(label="x", route_id="route"),
        PremiumButton(sku_id=1),
    ],
)
def test_a_bare_control_reaches_the_scene(control: LinkButton | RoutedButton | PremiumButton) -> None:
    result = plan(control, target=DISCORD_V2_DPY27)
    (row,) = result.scene.body.children
    assert isinstance(row, scene.Row)
    assert len(row.items) == 1
    wire = scene.Codec.dumps(result.scene)
    if isinstance(control, LinkButton):
        assert control.url in wire
    elif isinstance(control, RoutedButton):
        assert control.route_id in wire
    else:
        assert isinstance(row.items[0], scene.PremiumButton)
        assert row.items[0].sku_id == control.sku_id
