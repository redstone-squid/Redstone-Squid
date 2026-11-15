"""Explicit query indexes share scopes while mutable native views stay fresh by default."""

from typing import override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui import testing as engine
from squid_ui.ext.discord import testing
from squid_ui.selectors import TreeIndex, query, select


def test_render_index_preserves_component_occurrences() -> None:
    component = engine.text_component("Ready")
    index = engine.render_index(component)

    assert isinstance(index, TreeIndex)
    assert query(index, select(component)).one().node is component
    assert query(index, 'Paragraph[text="Ready"]').count() == 1
    assert not hasattr(engine, "render_snapshot")


async def test_native_index_reuses_scopes_but_new_queries_observe_view_mutations() -> None:
    first = discord.ui.Button(label="First", custom_id="first")
    second = discord.ui.Button(label="Second", custom_id="second")
    row = discord.ui.ActionRow(first, second)
    view = discord.ui.LayoutView().add_item(row)
    index = testing.index_tree(view)
    parent = testing.query(index, select(row)).one()
    matches = testing.query(index, "Button", within=parent).all()

    assert matches[0].precedes(matches[1])
    assert testing.index_tree(index) is index
    assert not hasattr(testing, "snapshot")
    row.add_item(discord.ui.Button(label="Third", custom_id="third"))
    assert testing.query(view, "Button").count() == 3
    assert testing.query(index, "Button").count() == 2
    first.label = "Renamed"
    assert testing.query(index, 'Button[label="Renamed"]').one().node is first


class IndexedScreen(sd.Screen[None, object]):
    @override
    def render(self) -> sl.LayoutNode:
        return sl.stack(sl.paragraph("First"), sl.paragraph("Second"))


async def test_staged_index_can_be_reused_for_scoping_and_order() -> None:
    async with testing.stage(IndexedScreen(), owner=object()) as staged:
        index = staged.index()
        first, second = staged.query("TextDisplay", view=index).all()
        assert first.precedes(second)
        assert staged.query(":scope", view=index, within=first, include_scope=True).one() == first
        assert isinstance(staged.index(), TreeIndex)
        assert not hasattr(staged, "snapshot")
