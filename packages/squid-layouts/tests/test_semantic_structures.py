"""Semantic structures select legal Discord representations."""

from squid_layouts import PresentationSession, plan
from squid_layouts.discord import DISCORD_V2
from squid_layouts.scene import SceneGallery, SceneRow, SceneSelect, SceneText
from squid_layouts.semantic import (
    Choice,
    Choices,
    Column,
    Destination,
    Details,
    Item,
    Items,
    Media,
    MediaItem,
    Navigation,
    Paragraph,
    Table,
    TableRow,
)


async def _change(_event) -> None: ...


def test_small_single_choices_use_buttons_and_larger_sets_use_a_picker() -> None:
    small = Choices("size", tuple(Choice(str(index), str(index)) for index in range(3)), (), _change)
    large = Choices("size", tuple(Choice(str(index), str(index)) for index in range(6)), (), _change)

    assert isinstance(plan(small, target=DISCORD_V2).scene.children[0], SceneRow)
    assert isinstance(plan(large, target=DISCORD_V2).scene.children[0], SceneSelect)


def test_items_switch_from_overview_to_focused_content_through_session_state() -> None:
    session = PresentationSession()
    document = Items(
        "catalog",
        (
            Item("one", "One", (Paragraph("first detail"),), "first"),
            Item("two", "Two", (Paragraph("second detail"),), "second"),
        ),
    )
    overview = plan(document, target=DISCORD_V2, session=session)
    session.select("catalog", ("two",))
    focused = plan(document, target=DISCORD_V2, session=session)

    assert any(isinstance(node, SceneSelect) for node in overview.scene.children)
    assert any(isinstance(node, SceneText) and "second detail" in node.content for node in focused.scene.children)


def test_details_disclosure_is_presentation_state() -> None:
    session = PresentationSession()
    document = Details("debug", "Debug details", (Paragraph("hidden body"),))

    closed = plan(document, target=DISCORD_V2, session=session)
    session.disclose("debug", open_=True)
    opened = plan(document, target=DISCORD_V2, session=session)

    assert not any(isinstance(node, SceneText) and "hidden body" in node.content for node in closed.scene.children)
    assert any(isinstance(node, SceneText) and "hidden body" in node.content for node in opened.scene.children)


def test_navigation_groups_six_destinations() -> None:
    document = Navigation(
        "tabs",
        tuple(Destination(str(index), f"Tab {index}") for index in range(6)),
        "0",
        _change,
    )

    assert isinstance(plan(document, target=DISCORD_V2).scene.children[0], SceneSelect)


def test_tables_and_media_choose_mechanical_target_shapes() -> None:
    table = Table(
        (Column("name", "Name"), Column("value", "Value")),
        (TableRow("one", ("Alpha", "1")), TableRow("two", ("Beta", "2"))),
        "stats",
    )
    media = Media(tuple(MediaItem(str(index), f"https://example.invalid/{index}.png") for index in range(12)), "shots")

    table_scene = plan(table, target=DISCORD_V2).scene
    media_scene = plan(media, target=DISCORD_V2).scene

    assert isinstance(table_scene.children[0], SceneText)
    assert table_scene.children[0].content.startswith("```")
    galleries = [node for node in media_scene.children if isinstance(node, SceneGallery)]
    assert [len(gallery.items) for gallery in galleries] == [10, 2]
