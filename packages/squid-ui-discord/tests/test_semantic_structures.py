"""Semantic structures select legal Discord representations."""

import discord
import pytest

import squid_ui as sl
from squid_ui_discord import DISCORD_V2_DPY27, render_static
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import plan
from squid_ui.runtime import PresentationSession, apply_updates
from squid_ui.semantic import (
    Choice,
    Choices,
    Column,
    Details,
    Field,
    Fields,
    Item,
    Items,
    Media,
    MediaItem,
    Navigation,
    NavOption,
    Note,
    Paragraph,
    Section,
    Table,
    TableRow,
)
from squid_ui.sources import Position


async def _change(_event) -> None: ...


def test_small_single_choices_use_buttons_and_larger_sets_use_a_picker() -> None:
    small = Choices("size", tuple(Choice(str(index), str(index)) for index in range(3)))
    large = Choices("size", tuple(Choice(str(index), str(index)) for index in range(6)))

    assert isinstance(plan(small, target=DISCORD_V2_DPY27).scene.components_v2.children[0], scene.Row)
    assert isinstance(plan(large, target=DISCORD_V2_DPY27).scene.components_v2.children[0], scene.Select)


def test_items_switch_from_overview_to_focused_content_through_session_state() -> None:
    session = PresentationSession()
    document = Items(
        "catalog",
        (
            Item("one", sl.semantic.ItemLabel("One"), (Paragraph("first detail"),), "first"),
            Item("two", sl.semantic.ItemLabel("Two"), (Paragraph("second detail"),), "second"),
        ),
    )
    overview = plan(document, target=DISCORD_V2_DPY27, session=session)
    session.select("catalog", ("two",))
    focused = plan(document, target=DISCORD_V2_DPY27, session=session)

    assert any(isinstance(node, scene.Select) for node in overview.scene.components_v2.children)
    assert any(
        isinstance(node, scene.Text) and "second detail" in node.content
        for node in focused.scene.components_v2.children
    )


def test_details_disclosure_is_presentation_state() -> None:
    session = PresentationSession()
    document = Details("debug", sl.semantic.Summary("Debug details"), (Paragraph("hidden body"),))

    closed = plan(document, target=DISCORD_V2_DPY27, session=session)
    session.disclose("debug", open_=True)
    opened = plan(document, target=DISCORD_V2_DPY27, session=session)

    assert not any(
        isinstance(node, scene.Text) and "hidden body" in node.content for node in closed.scene.components_v2.children
    )
    assert any(
        isinstance(node, scene.Text) and "hidden body" in node.content for node in opened.scene.components_v2.children
    )


def test_an_unset_selection_is_distinguishable_from_an_empty_one() -> None:
    session = PresentationSession()

    assert session.selection("catalog", initial=("two",)).selected == ("two",)
    session.select("catalog", ())
    assert session.selection("catalog", initial=("two",)).selected == ()


def test_navigation_groups_six_destinations() -> None:
    document = Navigation("tabs", tuple(NavOption(str(index), f"Tab {index}") for index in range(6)))

    assert isinstance(plan(document, target=DISCORD_V2_DPY27).scene.components_v2.children[0], scene.Select)


def test_large_semantic_pickers_fold_into_keyed_25_and_11_pages() -> None:
    choices = Choices("size", tuple(Choice(str(index), f"Choice {index}") for index in range(36)))
    items = Items(
        "catalog",
        tuple(
            Item(str(index), sl.semantic.ItemLabel(f"Item {index}"), (Paragraph(f"Detail {index}"),))
            for index in range(36)
        ),
    )
    navigation = Navigation("tabs", tuple(NavOption(str(index), f"Tab {index}") for index in range(36)))

    choice_plan = plan(choices, target=DISCORD_V2_DPY27, positions={"size.choices": Position(offset=1)})
    item_plan = plan(items, target=DISCORD_V2_DPY27, positions={"catalog.items": Position(offset=1)})
    navigation_plan = plan(navigation, target=DISCORD_V2_DPY27, positions={"tabs.destinations": Position(offset=1)})

    choice_select = next(node for node in choice_plan.scene.components_v2.children if isinstance(node, scene.Select))
    item_select = next(node for node in item_plan.scene.components_v2.children if isinstance(node, scene.Select))
    navigation_select = next(
        node for node in navigation_plan.scene.components_v2.children if isinstance(node, scene.Select)
    )
    assert [len(choice_select.options), len(item_select.options), len(navigation_select.options)] == [11, 11, 11]
    assert [(pager.key, pager.page, pager.pages) for pager in choice_plan.scene.pagers] == [("size.choices", 1, 2)]
    assert [(pager.key, pager.page, pager.pages) for pager in item_plan.scene.pagers] == [("catalog.items", 1, 2)]
    assert [(pager.key, pager.page, pager.pages) for pager in navigation_plan.scene.pagers] == [
        ("tabs.destinations", 1, 2)
    ]


def test_keyed_item_page_stays_with_its_anchor_when_entries_are_inserted() -> None:
    session = PresentationSession()
    original = tuple(
        Item(str(index), sl.semantic.ItemLabel(f"Item {index}"), (Paragraph("detail"),)) for index in range(36)
    )
    first = plan(Items("catalog", original), target=DISCORD_V2_DPY27, session=session)
    apply_updates(session, first.session_updates)
    session.move_cursor("catalog.items", Position(offset=1))
    second_page = plan(Items("catalog", original), target=DISCORD_V2_DPY27, session=session)
    apply_updates(session, second_page.session_updates)
    assert (
        "25"
        in next(node for node in second_page.scene.components_v2.children if isinstance(node, scene.Select))
        .options[0]
        .value
    )

    inserted = (Item("new", sl.semantic.ItemLabel("New"), (Paragraph("detail"),)), *original)
    replanned = plan(Items("catalog", inserted), target=DISCORD_V2_DPY27, session=session)
    values = {
        option.value
        for node in replanned.scene.components_v2.children
        if isinstance(node, scene.Select)
        for option in node.options
    }
    assert "25" in values


def test_choices_minimum_zero_allows_deselecting_all() -> None:
    document = Choices("size", tuple(Choice(str(index), str(index)) for index in range(6)), minimum=0)

    select = next(
        node
        for node in plan(document, target=DISCORD_V2_DPY27).scene.components_v2.children
        if isinstance(node, scene.Select)
    )
    assert select.min_values == 0


def test_cross_page_multi_choice_requires_an_explicit_grouping_model() -> None:
    document = Choices("many", tuple(Choice(str(index), str(index)) for index in range(36)), maximum=2)

    with pytest.raises(LayoutInvariantError, match="cross-page multi-selection is ambiguous"):
        plan(document, target=DISCORD_V2_DPY27)


def test_tables_and_media_choose_mechanical_target_shapes() -> None:
    table = Table(
        sl.semantic.Columns((Column("name", "Name"), Column("value", "Value"))),
        (TableRow("one", ("Alpha", "1")), TableRow("two", ("Beta", "2"))),
        "stats",
    )
    media = Media(tuple(MediaItem(str(index), f"https://example.invalid/{index}.png") for index in range(12)), "shots")

    table_scene = plan(table, target=DISCORD_V2_DPY27).scene
    media_scene = plan(media, target=DISCORD_V2_DPY27).scene

    assert isinstance(table_scene.components_v2.children[0], scene.Text)
    assert table_scene.components_v2.children[0].content.startswith("```")
    galleries = [node for node in media_scene.components_v2.children if isinstance(node, scene.Gallery)]
    assert [len(gallery.items) for gallery in galleries] == [10, 2]


def test_a_section_carries_house_colour_a_lead_image_and_small_print() -> None:
    document = Section(
        sl.semantic.Heading("Title"),
        (Paragraph("body"), Note("Submission ID: 5")),
        accent=0x43B581,
        thumbnail="https://example.invalid/lead.png",
    )

    planned = plan(document, target=DISCORD_V2_DPY27).scene
    panel = planned.components_v2.children[0]

    assert isinstance(panel, scene.Panel)
    assert panel.accent == 0x43B581
    lead = panel.children[0]
    assert isinstance(lead, scene.Section)
    assert lead.texts[0].content == "## Title"
    assert lead.accessory == scene.Thumbnail("https://example.invalid/lead.png")
    assert panel.children[-1] == scene.Text("-# Submission ID: 5")


def test_nested_sections_flatten_inside_the_outer_discord_container() -> None:
    document = Section(
        sl.semantic.Heading("Help"),
        (
            Paragraph("Choose a workflow."),
            Section(
                sl.semantic.Heading("Build"),
                (Fields((Field("build", "/build", "Submit a build."),)),),
            ),
            Section(
                sl.semantic.Heading("Discover"),
                (Fields((Field("search", "/search", "Find a build."),)),),
            ),
        ),
    )

    presentation = render_static(document)
    panel = presentation.layout.to_components()[0]

    assert panel["type"] == discord.ComponentType.container.value
    assert all(
        child["type"]
        in {
            discord.ComponentType.action_row.value,
            discord.ComponentType.section.value,
            discord.ComponentType.text_display.value,
            discord.ComponentType.media_gallery.value,
            discord.ComponentType.file.value,
            discord.ComponentType.separator.value,
        }
        for child in panel["components"]
    )
    assert "Build" in str(panel) and "Discover" in str(panel)


def test_palette_resolves_inherited_exact_and_explicitly_absent_accents() -> None:
    document = (
        sl.block("inherited"),
        sl.block("absent", accent=None),
        sl.block("exact", accent=0x123456),
        sl.aside("semantic", tone=sl.Tone.WARNING),
    )

    panels = plan(
        document,
        target=DISCORD_V2_DPY27,
        palette=sl.Palette(brand=0xABCDEF, warning=0x654321),
    ).scene.components_v2.children

    assert [panel.accent for panel in panels if isinstance(panel, scene.Panel)] == [
        0xABCDEF,
        None,
        0x123456,
        0x654321,
    ]


def test_palette_scope_is_dynamic_and_does_not_leak_to_siblings() -> None:
    document = (
        sl.block("outer before"),
        sl.themed(sl.Palette(brand=0x222222), sl.block("inner")),
        sl.block("outer after"),
    )

    panels = plan(document, target=DISCORD_V2_DPY27, palette=sl.Palette(brand=0x111111)).scene.components_v2.children

    assert [panel.accent for panel in panels if isinstance(panel, scene.Panel)] == [0x111111, 0x222222, 0x111111]


def test_a_lead_image_sits_beside_the_required_heading() -> None:
    document = Section(sl.semantic.Heading("Title"), (Paragraph("body"),), thumbnail="https://example.invalid/lead.png")

    panel = plan(document, target=DISCORD_V2_DPY27).scene.components_v2.children[0]

    assert isinstance(panel, scene.Panel)
    assert panel.children[0] == scene.Section(
        (scene.Text("## Title"),), scene.Thumbnail("https://example.invalid/lead.png")
    )
    assert panel.children[1] == scene.Text("body")


def test_fields_step_down_their_own_ladders_and_never_lose_a_field() -> None:
    document = Section(
        sl.semantic.Heading("Fields"),
        (Fields(tuple(Field(str(index), f"Field {index}", "v" * 400, fallbacks=("short",)) for index in range(20))),),
    )

    panel = plan(document, target=DISCORD_V2_DPY27).scene.components_v2.children[0]

    assert isinstance(panel, scene.Panel)
    body = "\n".join(child.content for child in panel.children if isinstance(child, scene.Text))
    assert all(f"**Field {index}:**" in body for index in range(20))
    assert "short" in body
