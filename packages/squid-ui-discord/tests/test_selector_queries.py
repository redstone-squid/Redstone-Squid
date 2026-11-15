"""Native Discord queries preserve hierarchy and inspect only delivered staged output."""

from typing import assert_type, override

import discord
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.ext.discord import testing
from squid_ui.selectors import Match, QueryError, select


async def test_native_views_preserve_rows_sections_and_accessories() -> None:
    save = discord.ui.Button(label="Save", custom_id="save")
    extra = discord.ui.Button(label="More", custom_id="more")
    row = discord.ui.ActionRow(save)
    section = discord.ui.Section(discord.ui.TextDisplay("Details"), accessory=extra)
    view = discord.ui.LayoutView().add_item(discord.ui.Container(row, section))

    assert testing.query(view, 'ActionRow > Button[custom_id="save"]').one().node is save
    assert testing.query(view, 'Section > Button[label="More"]').one().node is extra
    assert testing.query(view, select(discord.ui.TextDisplay)).one().node.content == "Details"
    assert not testing.query(view, "Container > Button").exists()
    captured = testing.index_tree(view)
    section_match = testing.query(captured, select(section)).one()
    found = testing.query(captured, select(discord.ui.Button), within=section_match).one()
    assert_type(found, Match[discord.ui.Button])
    assert found.node is extra
    assert not testing.query(captured, "ActionRow Button", within=section_match).exists()
    assert testing.index_tree(captured) is captured


async def test_classic_views_and_modal_labels_are_queryable() -> None:
    button = discord.ui.Button(label="Click", custom_id="click")
    view = discord.ui.View().add_item(button)
    assert testing.query(view, "View > Button").one().node is button
    field = discord.ui.TextInput(custom_id="name")
    modal = discord.ui.Modal(title="Edit")
    modal.add_item(discord.ui.Label(text="Name", component=field))
    assert testing.query(modal, "Modal > Label > TextInput").one().node is field
    assert testing.query(view, "Control", types={"Control": discord.ui.Button}).one().node is button


async def test_native_select_options_remain_children_of_the_select() -> None:
    option = discord.SelectOption(label="One", value="one")
    menu = discord.ui.Select(options=[option], custom_id="pick")
    assert testing.query(menu, 'Select > SelectOption[value="one"]').one().node is option
    with pytest.raises(QueryError, match="found 0"):
        testing.query(menu, "Button").one()


class Counter(sd.Screen[None, object]):
    count: int = sl.state(0)

    @override
    def render(self) -> sl.LayoutNode:
        return sl.stack(
            sl.paragraph(f"Count {self.count}"),
            sl.action_controls(sl.action_control("Add", self.add, key="add"), key="actions"),
        )

    async def add(self, _event: sl.PressEvent) -> None:
        self.count += 1


async def test_staged_queries_read_delivery_without_rendering_pending_state() -> None:
    counter = Counter()
    async with testing.stage(counter, owner=object()) as staged:
        assert staged.query('TextDisplay[content="Count 0"]').exists()
        before = staged.index()
        counter.count = 10
        assert staged.root.pending
        # Even an explicitly rendered candidate must not replace the delivered output.
        candidate = staged.root._stage_view()
        assert staged.query('TextDisplay[content="Count 10"]', view=candidate).exists()
        assert staged.query('TextDisplay[content="Count 0"]').exists()
        assert not staged.query('TextDisplay[content="Count 10"]').exists()
        assert testing.query(before, 'TextDisplay[content="Count 0"]').exists()


async def test_staged_queries_resolve_again_after_delivery() -> None:
    async with testing.stage(Counter(), owner=object()) as staged:
        assert staged.query(select(discord.ui.Button).label("Add")).one().node.label == "Add"
        await staged.press("add")
        assert staged.query('TextDisplay[content="Count 1"]').exists()
        captured = staged.index()
        container = staged.query("LayoutView", view=captured).one()
        assert staged.query("Button", view=captured, within=container).count() == 1
