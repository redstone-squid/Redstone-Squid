"""Direct child composition uses the mounted Discord lifecycle and dispatcher."""

from typing import override

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui_discord.testing import stage


class Counter(sl.Component[sl.ComponentsV2Target, sd.Request]):
    value: int = sl.state(0)

    def __init__(self) -> None:
        self.loads = 0

    @override
    async def on_load(self) -> None:
        self.loads += 1
        self.value = 10

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.block(
            sl.paragraph(str(self.value)),
            sl.action_controls(sl.action_control("Increment", self.increment, key="increment"), key="actions"),
        )

    async def increment(self, _event: sd.PressEvent) -> None:
        self.value += 1


class Parent(sd.Screen):
    reversed: bool = sl.state(default=False)
    shown: bool = sl.state(default=True)

    def __init__(self) -> None:
        self.first = Counter()
        self.second = Counter()

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        children = (self.first, self.second.keyed("named")) if self.shown else ()
        return sl.block(
            *(tuple(reversed(children)) if self.reversed else children),
            sl.action_controls(
                sl.action_control("Reverse", self.reverse, key="reverse"),
                sl.action_control("Show/hide", self.toggle, key="toggle"),
                key="parent-actions",
            ),
        )

    async def reverse(self, _event: sd.PressEvent) -> None:
        self.reversed = not self.reversed

    async def toggle(self, _event: sd.PressEvent) -> None:
        self.shown = not self.shown


async def test_direct_children_load_dispatch_reorder_and_remount() -> None:
    parent = Parent()
    async with stage(parent, owner=object()) as ui:
        keys = ui.root.snapshot().handler_keys
        direct_key = next(key for key in keys if key.endswith(".increment") and key != "named.increment")
        assert parent.first.loads == parent.second.loads == 1
        assert parent.first.value == parent.second.value == 10

        await ui.press(direct_key)
        await ui.press("reverse")
        await ui.press(direct_key)
        assert parent.first.value == 12
        assert parent.second.value == 10
        await ui.press("named.increment")
        assert parent.second.value == 11

        await ui.press("toggle")
        assert direct_key not in ui.root.snapshot().handler_keys
        await ui.press("toggle")
        ui.control(direct_key)
        assert parent.first.loads == parent.second.loads == 1
        assert parent.first.value == 12
        assert parent.second.value == 11
