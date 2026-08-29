"""Pins render-target propagation through widget content and both machine shells.

Every ignored line is an assertion that the checker rejects a target mismatch. An unused
suppression means a generic parameter stopped reaching the component or rendered document.
"""

from typing import assert_type

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui.document import DocumentLike
from squid_ui.planning import ClassicTarget, ComponentsV2Target, plan
from squid_ui.primitives import Panel, Text
from squid_ui_discord.target import classic, v2

v2_tab = sp.Tab("details", "Details", Panel((Text("V2 only"),)))
assert_type(v2_tab, sp.Tab[ComponentsV2Target])

v2_tabs = sp.Tabs((v2_tab,), key="tabs")
assert_type(v2_tabs, sp.Tabs[ComponentsV2Target])

v2_component = v2_tabs.build_component()
assert_type(v2_component, sp.ComponentDriver[sp.TabsState, ComponentsV2Target])

portable_tabs = sp.Tabs((sp.Tab("details", "Details", sl.paragraph("portable")),), key="tabs")
assert_type(portable_tabs, sp.Tabs[sl.RenderTarget])


def accepts_classic(component: sl.Component[ClassicTarget]) -> None:
    del component


accepts_classic(portable_tabs.build_component())
accepts_classic(v2_component)  # pyrefly: ignore[bad-argument-type]

routed = sp.RouteDriver[sp.TabsState, ComponentsV2Target](lambda route: route.action).render(
    v2_tabs, v2_tabs.initial_state
)
assert_type(routed, DocumentLike[ComponentsV2Target])
plan(routed, target=v2())
plan(routed, target=classic())  # pyrefly: ignore[no-matching-overload, bad-argument-type]
