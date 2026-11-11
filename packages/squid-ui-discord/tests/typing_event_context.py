"""Frontend constraints survive callbacks, containers, and screen composition."""

from typing import assert_type

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.semantic import ActionControl


async def discord(event: sd.PressEvent) -> None:
    assert_type(event.context, sd.Request)


async def portable(event: sl.PressEvent) -> None:
    assert_type(event.context, object)


control = sl.action_control("Discord", discord, key="discord")
assert_type(control, ActionControl[sd.Request])
node = sl.stack(sl.action_controls(control, key="actions"))
compatible: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = node
# A portable document must accept every invocation context.
incompatible: sl.LayoutNode = node  # pyrefly: ignore[bad-assignment]
portable_node = sl.action_controls(sl.action_control("Portable", portable, key="portable"), key="actions")
portable_on_discord: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = portable_node


class OtherContext:
    pass


# Matching render targets do not make unrelated invocation contexts compatible.
other: sl.LayoutNode[sl.ComponentsV2Target, OtherContext] = node  # pyrefly: ignore[bad-assignment]


class DiscordPanel(sd.Screen):
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return node


# The context constraint survives a component boundary, too.
other_component: sl.Component[sl.ComponentsV2Target, OtherContext] = DiscordPanel()  # pyrefly: ignore[bad-assignment]


async def other_press(event: sl.PressEvent[OtherContext]) -> None:
    pass


# A frontend registration validates the callback's context type.
wrong_registration = sd.on_press(other_press)  # pyrefly: ignore[bad-argument-type]


async def incompatible_response(
    request: sd.Request, component: sl.Component[sl.ComponentsV2Target, OtherContext]
) -> None:
    # The public response boundary must not erase an incompatible context.
    await request.respond(component)  # pyrefly: ignore[no-matching-overload]


# Direct and keyed placement preserve the child's required event context.
direct_child: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = DiscordPanel()
keyed_child: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = DiscordPanel().keyed("discord")
wrong_direct: sl.LayoutNode[sl.ComponentsV2Target, OtherContext] = DiscordPanel()  # type: ignore  # Deliberately incompatible target or context.
wrong_keyed: sl.LayoutNode[sl.ComponentsV2Target, OtherContext] = DiscordPanel().keyed("discord")  # type: ignore  # Deliberately incompatible target or context.
