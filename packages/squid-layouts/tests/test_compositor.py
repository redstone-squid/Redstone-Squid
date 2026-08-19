"""The compositor: one pipeline, and the reserved-budget contract composed views rely on."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_solve import documents

from squid_layouts import LIMITS, Button, Panel, Row, Text, compose, render_static
from squid_layouts.compositor import Composition


def _display_text(view: discord.ui.LayoutView) -> int:
    return sum(len(item.content) for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


class TestCompose:
    def test_returns_the_solved_layout_beside_the_view(self):
        composition = compose([Text("hello")])
        assert isinstance(composition, Composition)
        assert composition.interventions == []
        assert composition.solved.notes == []
        assert composition.pages == 1
        assert composition.page == 0

    def test_static_documents_reject_interactive_nodes(self):
        async def click(interaction: discord.Interaction) -> None: ...

        with pytest.raises(TypeError, match="Mount"):
            compose([Row((Button(label="x", on_click=click),))])

    def test_it_fills_a_supplied_view(self):
        view = discord.ui.LayoutView(timeout=None)
        composition = compose([Text("hello")], into=view)
        assert composition.view is view


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
