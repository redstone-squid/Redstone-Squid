"""Materialized control windows use region limits and runtime-owned selection."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_ui.pages import InitialPage
from squid_ui.planning import plan
from squid_ui.runtime import ComponentRuntime, PresentationState


def test_region_can_page_atomic_control_windows() -> None:
    document = sl.region(
        *(sl.unbreakable(sl.choices(sl.choice(f"Choice {i}", key=str(i)), key=f"window-{i}")) for i in range(3)),
        key="windows",
        title="",
        page_size=1,
    )
    result = plan(document, target=html.target(), inspect_layout=True)
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("windows"))
    assert region.count == sl.Known(3)
    assert "Choice 0" in html.Renderer().draw(result.scene)
    assert "Choice 2" not in html.Renderer().draw(result.scene)
    later = result.for_page({region.identity: 2})
    assert "Choice 2" in html.Renderer().draw(later.scene)
    assert "Choice 0" not in html.Renderer().draw(later.scene)


def test_region_window_size_is_positive() -> None:
    with pytest.raises(ValueError, match="page_size must be positive"):
        sl.region(key="windows", title="", page_size=0)


@pytest.mark.parametrize("kind,key", [(sl.semantic.ActionControl, "row-2"), (sl.semantic.ActionControls, "actions-2")])
async def test_initial_anchor_uses_namespaced_content_and_is_consumed_after_delivery(
    kind: type[object], key: str
) -> None:
    class Rows(sl.Component):
        @override
        def render(self) -> sl.LayoutNode:
            async def pressed(event: sl.PressEvent) -> None:
                await event.acknowledge()

            return sl.region(
                *(
                    sl.action_controls(
                        sl.action_control(str(i), pressed, key=f"row-{i}"), key=f"actions-{i}"
                    ).unbreakable()
                    for i in range(3)
                ),
                key="rows",
                title="",
                page_size=1,
                page=InitialPage(anchor=sl.element_ref(kind, key)),
            )

    class Screen(sl.Component):
        def __init__(self) -> None:
            self.rows = Rows()

        @override
        def render(self) -> sl.LayoutNode:
            return self.rows.keyed("nested")

    runtime = ComponentRuntime(Screen())
    session = PresentationState()
    document = runtime.render().nodes
    first = plan(document, target=html.target(), session=session, inspect_layout=True)
    assert first.scene.pagers[0].page == 2
    assert not session.cursors
    session.apply_updates(first.session_updates)
    assert first.layout is not None
    region = first.layout.region(sl.region_ref("nested.rows"))
    previous = first.for_page({region.identity: 0})
    session.apply_updates(previous.session_updates)
    assert plan(document, target=html.target(), session=session).scene.pagers[0].page == 0
    runtime.finish()
