"""Pins render targets through tree expansion and runtime ownership; nothing here runs."""

from typing import assert_type, override

from squid_ui.ext.discord.primitives import Panel
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.runtime.definition import Component
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.runtime.owner import ComponentRuntime
from squid_ui.runtime.tree import ComponentTree


class V2Component(Component[ComponentsV2Target]):
    @override
    def render(self) -> Panel:
        return Panel(())


component = V2Component()
assert_type(render_component_tree(component), ComponentTree[ComponentsV2Target])

runtime = ComponentRuntime(component)
assert_type(runtime, ComponentRuntime[ComponentsV2Target])
assert_type(runtime.render(), ComponentTree[ComponentsV2Target])
