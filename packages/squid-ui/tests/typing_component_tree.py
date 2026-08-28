"""Pins render targets through tree expansion and runtime ownership; nothing here runs."""

from typing import assert_type

from squid_ui.planning import ComponentsV2Target
from squid_ui.primitives import Panel
from squid_ui.runtime.component import Component, ComponentTree, render_component_tree
from squid_ui.runtime.owner import ComponentRuntime


class V2Component(Component[ComponentsV2Target]):
    def render(self) -> Panel:
        return Panel(())


component = V2Component()
assert_type(render_component_tree(component), ComponentTree[ComponentsV2Target])

runtime = ComponentRuntime(component)
assert_type(runtime, ComponentRuntime[ComponentsV2Target])
assert_type(runtime.render(), ComponentTree[ComponentsV2Target])
