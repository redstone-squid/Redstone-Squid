"""The render pipeline and the reserved-budget contract embedded regions rely on."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_ui import PressEvent
from squid_ui.planning.limits import Axis
from squid_ui.primitives import (
    Button,
    Code,
    Drop,
    Footer,
    Heading,
    Lines,
    Never,
    Panel,
    Row,
    Sep,
    Spill,
    Text,
    Truncate,
)
from squid_ui_discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui_discord import (
    ResourceCost,
    render_message,
    render_static,
)
from squid_ui_discord.rendering import RenderedMessage

_policies = st.sampled_from([Truncate(), Truncate(keep="tail"), Spill(), Drop(), Never()])
_content = st.text(max_size=1500)


@st.composite
def documents(draw) -> list:
    """Small mixed documents: enough shapes to exercise every allocation path."""
    node = st.one_of(
        st.builds(Text, _content, overflow=_policies, priority=st.integers(-10, 10)),
        st.builds(Heading, _content),
        st.builds(Footer, _content),
        st.builds(Code, _content, lang=st.sampled_from(["", "py"])),
        st.builds(Lines, st.lists(st.text(min_size=1, max_size=120), max_size=40).map(tuple)),
        st.builds(Sep),
        st.builds(lambda content: Panel(children=(Text(content), Sep())), _content),
    )
    return draw(st.lists(node, max_size=6))


def _display_text(view: discord.ui.LayoutView) -> int:
    return sum(len(item.content) for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


class TestRenderMessage:
    def test_returns_the_solved_layout_beside_the_view(self):
        rendered = render_message([Text("hello")])
        assert isinstance(rendered, RenderedMessage)
        assert rendered.plan.report.events == ()
        assert rendered.pages == 1
        assert rendered.page == 0

    def test_static_documents_reject_interactive_nodes(self):
        async def click(event: PressEvent) -> None: ...

        with pytest.raises(TypeError, match="mounted Discord frontend"):
            render_message([Row((Button(label="x", on_click=click, key="x"),))])

    def test_it_rejects_native_view_adoption(self):
        with pytest.raises(TypeError, match="unexpected keyword argument 'into'"):
            render_message([Text("hello")], into=discord.ui.LayoutView(timeout=None))  # type: ignore[call-arg]


@given(documents(), st.integers(min_value=0, max_value=3900))
def test_reserved_text_is_held_back_from_the_budget(nodes, reserved):
    view = render_message(nodes, reservation=ResourceCost({Axis.DISPLAY_TEXT: reserved})).view
    assert _display_text(view) <= LIMITS.total_text - reserved


@given(documents())
def test_render_static_matches_render_message(nodes):
    assert render_static(nodes).layout.to_components() == render_message(nodes).view.to_components()


def test_a_reserved_budget_survives_nesting():
    # The host case: one card composed into a message whose other half the solver never sees.
    body = Panel(children=(Text("x" * 5000),))
    assert (
        _display_text(render_message([body], reservation=ResourceCost({Axis.DISPLAY_TEXT: 1000})).view)
        <= LIMITS.total_text - 1000
    )
