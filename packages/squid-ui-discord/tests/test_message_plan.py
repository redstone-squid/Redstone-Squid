"""Message preparation resolves only the controls the reader can actually use."""

import pytest

from squid_ui import scene
from squid_ui.content.entity import EntityType
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.messages.candidates import _MessagePlan
from squid_ui.interactions import ActionBinding, ActionEvent
from squid_ui.planning.result import PlanReport, PlanResult


async def handle(event: ActionEvent) -> None:
    pass


@pytest.mark.parametrize(
    "body",
    [
        discord_scene.ComponentsV2(
            (
                discord_scene.Panel(
                    (
                        discord_scene.Row(
                            (discord_scene.Button("Save", "button"), discord_scene.RoutedButton("Open", "route"))
                        ),
                        discord_scene.Section(
                            (discord_scene.Text("Details"),), discord_scene.Button("Edit", "accessory")
                        ),
                        discord_scene.Select((discord_scene.Option("One", "1"),), "select"),
                        discord_scene.EntitySelect(EntityType.USER, "entity"),
                    )
                ),
            )
        ),
        discord_scene.ClassicMessage(
            rows=(
                discord_scene.ClassicRow(
                    (discord_scene.Button("Save", "button"), discord_scene.Button("Edit", "accessory"))
                ),
                discord_scene.ClassicRow((discord_scene.Select((discord_scene.Option("One", "1"),), "select"),)),
                discord_scene.ClassicRow((discord_scene.EntitySelect(EntityType.USER, "entity"),)),
                discord_scene.ClassicRow((discord_scene.RoutedButton("Open", "route"),)),
            )
        ),
    ],
)
def test_message_plan_keeps_visible_bindings(body: scene.Body) -> None:
    bindings = {key: ActionBinding(key, handle) for key in ("button", "accessory", "select", "entity", "hidden")}
    plan = PlanResult(scene.Scene(1, "discord", 1, body), bindings, PlanReport())

    message = _MessagePlan.from_plan(plan)

    assert message.plan is plan
    assert message.keys == {"button", "accessory", "select", "entity"}
    assert all(binding is bindings[key] for key, binding in message.bindings.items())


def test_message_plan_rejects_missing_visible_binding() -> None:
    body: scene.Body = discord_scene.ComponentsV2(
        (discord_scene.Section((discord_scene.Text("Details"),), discord_scene.Button("Edit", "missing")),)
    )
    plan = PlanResult(scene.Scene(1, "discord", 1, body), {}, PlanReport())

    with pytest.raises(LayoutInvariantError, match="scene action 'missing' has no binding"):
        _MessagePlan.from_plan(plan)
