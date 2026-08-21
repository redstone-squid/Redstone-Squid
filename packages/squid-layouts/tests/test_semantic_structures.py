"""Semantic structures select legal Discord representations."""

import pytest

from squid_layouts import (
    LayoutInvariantError,
    plan,
)
from squid_layouts.discord import DEFAULT_TARGET
from squid_layouts.runtime import PresentationSession, apply_updates
from squid_layouts.scene.model import (
    SceneGallery,
    SceneGalleryItem,
    ScenePanel,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.semantic import (
    Choice,
    Choices,
    Column,
    Destination,
    Details,
    Field,
    Fields,
    Item,
    Items,
    Media,
    MediaItem,
    Navigation,
    Note,
    Paragraph,
    Section,
    Table,
    TableRow,
)


async def _change(_event) -> None: ...


def test_small_single_choices_use_buttons_and_larger_sets_use_a_picker() -> None:
    small = Choices("size", tuple(Choice(str(index), str(index)) for index in range(3)))
    large = Choices("size", tuple(Choice(str(index), str(index)) for index in range(6)))

    assert isinstance(plan(small, target=DEFAULT_TARGET).scene.children[0], SceneRow)
    assert isinstance(plan(large, target=DEFAULT_TARGET).scene.children[0], SceneSelect)


def test_items_switch_from_overview_to_focused_content_through_session_state() -> None:
    session = PresentationSession()
    document = Items(
        "catalog",
        (
            Item("one", "One", (Paragraph("first detail"),), "first"),
            Item("two", "Two", (Paragraph("second detail"),), "second"),
        ),
    )
    overview = plan(document, target=DEFAULT_TARGET, session=session)
    session.select("catalog", ("two",))
    focused = plan(document, target=DEFAULT_TARGET, session=session)

    assert any(isinstance(node, SceneSelect) for node in overview.scene.children)
    assert any(isinstance(node, SceneText) and "second detail" in node.content for node in focused.scene.children)


def test_details_disclosure_is_presentation_state() -> None:
    session = PresentationSession()
    document = Details("debug", "Debug details", (Paragraph("hidden body"),))

    closed = plan(document, target=DEFAULT_TARGET, session=session)
    session.disclose("debug", open_=True)
    opened = plan(document, target=DEFAULT_TARGET, session=session)

    assert not any(isinstance(node, SceneText) and "hidden body" in node.content for node in closed.scene.children)
    assert any(isinstance(node, SceneText) and "hidden body" in node.content for node in opened.scene.children)


def test_an_unset_selection_is_distinguishable_from_an_empty_one() -> None:
    session = PresentationSession()

    assert session.selection("catalog", initial=("two",)).selected == ("two",)
    session.select("catalog", ())
    assert session.selection("catalog", initial=("two",)).selected == ()


def test_navigation_groups_six_destinations() -> None:
    document = Navigation("tabs", tuple(Destination(str(index), f"Tab {index}") for index in range(6)))

    assert isinstance(plan(document, target=DEFAULT_TARGET).scene.children[0], SceneSelect)


def test_large_semantic_pickers_fold_into_keyed_25_and_11_pages() -> None:
    choices = Choices("size", tuple(Choice(str(index), f"Choice {index}") for index in range(36)))
    items = Items(
        "catalog",
        tuple(Item(str(index), f"Item {index}", (Paragraph(f"Detail {index}"),)) for index in range(36)),
    )
    navigation = Navigation("tabs", tuple(Destination(str(index), f"Tab {index}") for index in range(36)))

    choice_plan = plan(choices, target=DEFAULT_TARGET, page={"size.choices": 1})
    item_plan = plan(items, target=DEFAULT_TARGET, page={"catalog.items": 1})
    navigation_plan = plan(navigation, target=DEFAULT_TARGET, page={"tabs.destinations": 1})

    choice_select = next(node for node in choice_plan.scene.children if isinstance(node, SceneSelect))
    item_select = next(node for node in item_plan.scene.children if isinstance(node, SceneSelect))
    navigation_select = next(node for node in navigation_plan.scene.children if isinstance(node, SceneSelect))
    assert [len(choice_select.options), len(item_select.options), len(navigation_select.options)] == [11, 11, 11]
    assert [(pager.key, pager.page, pager.pages) for pager in choice_plan.scene.pagers] == [("size.choices", 1, 2)]
    assert [(pager.key, pager.page, pager.pages) for pager in item_plan.scene.pagers] == [("catalog.items", 1, 2)]
    assert [(pager.key, pager.page, pager.pages) for pager in navigation_plan.scene.pagers] == [
        ("tabs.destinations", 1, 2)
    ]


def test_keyed_item_page_stays_with_its_anchor_when_entries_are_inserted() -> None:
    session = PresentationSession()
    original = tuple(Item(str(index), f"Item {index}", (Paragraph("detail"),)) for index in range(36))
    first = plan(Items("catalog", original), target=DEFAULT_TARGET, session=session)
    apply_updates(session, first.session_updates)
    session.move_cursor("catalog.items", 1)
    second_page = plan(Items("catalog", original), target=DEFAULT_TARGET, session=session)
    apply_updates(session, second_page.session_updates)
    assert "25" in next(node for node in second_page.scene.children if isinstance(node, SceneSelect)).options[0].value

    inserted = (Item("new", "New", (Paragraph("detail"),)), *original)
    replanned = plan(Items("catalog", inserted), target=DEFAULT_TARGET, session=session)
    values = {
        option.value for node in replanned.scene.children if isinstance(node, SceneSelect) for option in node.options
    }
    assert "25" in values


def test_cross_page_multi_choice_requires_an_explicit_grouping_model() -> None:
    document = Choices("many", tuple(Choice(str(index), str(index)) for index in range(36)), maximum=2)

    with pytest.raises(LayoutInvariantError, match="cross-page multi-selection is ambiguous"):
        plan(document, target=DEFAULT_TARGET)


def test_tables_and_media_choose_mechanical_target_shapes() -> None:
    table = Table(
        (Column("name", "Name"), Column("value", "Value")),
        (TableRow("one", ("Alpha", "1")), TableRow("two", ("Beta", "2"))),
        "stats",
    )
    media = Media(tuple(MediaItem(str(index), f"https://example.invalid/{index}.png") for index in range(12)), "shots")

    table_scene = plan(table, target=DEFAULT_TARGET).scene
    media_scene = plan(media, target=DEFAULT_TARGET).scene

    assert isinstance(table_scene.children[0], SceneText)
    assert table_scene.children[0].content.startswith("```")
    galleries = [node for node in media_scene.children if isinstance(node, SceneGallery)]
    assert [len(gallery.items) for gallery in galleries] == [10, 2]


def test_a_section_carries_house_colour_a_lead_image_and_small_print() -> None:
    document = Section(
        (Paragraph("body"), Note("Submission ID: 5")),
        heading="Title",
        accent=0x43B581,
        thumbnail="https://example.invalid/lead.png",
    )

    scene = plan(document, target=DEFAULT_TARGET).scene
    panel = scene.children[0]

    assert isinstance(panel, ScenePanel)
    assert panel.accent == 0x43B581
    lead = panel.children[0]
    assert isinstance(lead, SceneSection)
    assert lead.texts[0].content == "## Title"
    assert lead.accessory == SceneThumbnail("https://example.invalid/lead.png")
    assert panel.children[-1] == SceneText("-# Submission ID: 5")


def test_a_lead_image_with_no_heading_has_nothing_to_sit_beside() -> None:
    document = Section((Paragraph("body"),), thumbnail="https://example.invalid/lead.png")

    panel = plan(document, target=DEFAULT_TARGET).scene.children[0]

    assert isinstance(panel, ScenePanel)
    assert panel.children[0] == SceneGallery((SceneGalleryItem("https://example.invalid/lead.png"),))


def test_fields_step_down_their_own_ladders_and_never_lose_a_field() -> None:
    document = Section(
        (Fields(tuple(Field(str(index), f"Field {index}", "v" * 400, fallbacks=("short",)) for index in range(20))),),
    )

    panel = plan(document, target=DEFAULT_TARGET).scene.children[0]

    assert isinstance(panel, ScenePanel)
    body = "\n".join(child.content for child in panel.children if isinstance(child, SceneText))
    assert all(f"**Field {index}:**" in body for index in range(20))
    assert "short" in body
