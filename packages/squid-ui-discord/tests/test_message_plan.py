"""Message preparation resolves only the controls the reader can actually use."""

import pytest

from squid_ui import scene
from squid_ui.content.entity import EntityType
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.messages.candidates import _MessagePlan
from squid_ui.interactions import ActionBinding, ActionEvent
from squid_ui.planning.result import PlanReport, PlanResult


async def handle(event: ActionEvent) -> None:
    pass


@pytest.mark.parametrize(
    "body",
    [
        scene.ComponentsV2(
            (
                scene.Panel(
                    (
                        scene.Row((scene.Button("Save", "button"), scene.RoutedButton("Open", "route"))),
                        scene.Section((scene.Text("Details"),), scene.Button("Edit", "accessory")),
                        scene.Select((scene.Option("One", "1"),), "select"),
                        scene.EntitySelect(EntityType.USER, "entity"),
                    )
                ),
            )
        ),
        scene.ClassicMessage(
            rows=(
                scene.ClassicRow((scene.Button("Save", "button"), scene.Button("Edit", "accessory"))),
                scene.ClassicRow((scene.Select((scene.Option("One", "1"),), "select"),)),
                scene.ClassicRow((scene.EntitySelect(EntityType.USER, "entity"),)),
                scene.ClassicRow((scene.RoutedButton("Open", "route"),)),
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
    body: scene.Body = scene.ComponentsV2((scene.Section((scene.Text("Details"),), scene.Button("Edit", "missing")),))
    plan = PlanResult(scene.Scene(1, "discord", 1, body), {}, PlanReport())

    with pytest.raises(LayoutInvariantError, match="scene action 'missing' has no binding"):
        _MessagePlan.from_plan(plan)
