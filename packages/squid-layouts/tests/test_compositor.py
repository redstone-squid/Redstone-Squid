"""The compositor: one pipeline, and the reserved-budget contract composed views rely on."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import PressEvent
from squid_layouts.discord import (
    DEFAULT_LIMITS as LIMITS,
)
from squid_layouts.discord import (
    compose,
    render_static,
)
from squid_layouts.discord.compose import Composition
from squid_layouts.primitives import (
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


class TestCompose:
    def test_returns_the_solved_layout_beside_the_view(self):
        composition = compose([Text("hello")])
        assert isinstance(composition, Composition)
        assert composition.interventions == []
        assert composition.plan.report.events == ()
        assert composition.pages == 1
        assert composition.page == 0

    def test_static_documents_reject_interactive_nodes(self):
        async def click(event: PressEvent) -> None: ...

        with pytest.raises(TypeError, match="mounted Discord frontend"):
            compose([Row((Button(label="x", on_click=click, key="x"),))])

    def test_it_rejects_native_view_adoption(self):
        with pytest.raises(TypeError, match="unexpected keyword argument 'into'"):
            compose([Text("hello")], into=discord.ui.LayoutView(timeout=None))  # type: ignore[call-arg]


@given(documents(), st.integers(min_value=0, max_value=3900))
def test_reserved_text_is_held_back_from_the_budget(nodes, reserved):
    view = compose(nodes, reserved_text=reserved).view
    assert _display_text(view) <= LIMITS.total_text - reserved


@given(documents())
def test_render_static_matches_compose(nodes):
    assert render_static(nodes).to_components() == compose(nodes).view.to_components()


@given(documents())
def test_composed_documents_need_no_conform_interventions(nodes):
    # compose runs the gate itself; a non-empty result means the solver mismeasured.
    assert compose(nodes).interventions == []


def test_a_reserved_budget_survives_nesting():
    # The host case: one card composed into a message whose other half the solver never sees.
    body = Panel(children=(Text("x" * 5000),))
    assert _display_text(compose([body], reserved_text=1000).view) <= LIMITS.total_text - 1000
