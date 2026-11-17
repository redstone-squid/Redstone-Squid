"""Planned scenes expose display relationships while keeping binding metadata outside the tree."""

from dataclasses import dataclass

import pytest

from squid_ui import scene as s
from squid_ui.selectors import index_tree, query, select
from squid_ui.selectors._scene import is_scene_node, scene_children


def test_discord_scene_queries_reach_nested_controls_and_options() -> None:
    save = s.Button("Save", "save")
    body = s.ComponentsV2(
        (
            s.Panel(
                (
                    s.Section((s.Text("Summary"),), save),
                    s.Select((s.Option("Ada", "ada"),), "pick"),
                    s.Gallery((s.GalleryItem("https://example.test/image.png"),)),
                )
            ),
        )
    )
    scene = s.Scene(1, "discord", 1, body, pagers=(s.Pager("pages", 0, 2, "fingerprint"),))
    tree = index_tree(scene, children=scene_children)

    assert query(tree, select(s.Section).children(select(s.Button))).one().node is save
    assert query(tree, select(s.Select).children(select(s.Option).label("Ada"))).one().node.value == "ada"
    assert query(tree, select(s.GalleryItem)).count() == 1
    assert not query(tree, select(s.Pager)).exists()


def test_classic_scene_queries_preserve_embed_and_row_structure() -> None:
    field = s.EmbedField(name="Name", value="Ada")
    body = s.ClassicMessage(
        "Message",
        embeds=(s.Embed(fields=(field,), author=s.EmbedAuthor(name="Author"), footer=s.EmbedFooter(text="Footer")),),
        rows=(s.ClassicRow((s.Button("Save", "save"),)),),
    )
    tree = index_tree(body, children=scene_children)

    assert query(tree, select(s.Embed).children(select(s.EmbedField))).one().node is field
    assert query(tree, select(s.ClassicRow).children(select(s.Button))).one().node.action == "save"
    assert query(tree, select(s.EmbedAuthor)).count() == 1
    assert query(tree, select(s.EmbedFooter)).count() == 1


def test_html_scene_queries_visit_elements_and_text_but_not_references() -> None:
    text = s.HtmlText("Save")
    button = s.HtmlElement(
        s.HtmlTag.BUTTON,
        (text,),
        attributes=(s.HtmlAttribute(s.HtmlAttributeName.ID, "save"),),
        action=s.HtmlActionRef("save"),
        colour=s.HtmlColourRef(0x123456),
    )
    body = s.HtmlBody((s.HtmlElement(s.HtmlTag.MAIN, (button,)),))
    tree = index_tree(body, children=scene_children)

    assert (
        query(tree, select(s.HtmlElement).attr("tag", s.HtmlTag.BUTTON).has(select(s.HtmlText).text("Save"))).one().node
        is button
    )
    assert query(tree, select(s.HtmlText)).one().node is text
    assert not query(
        tree, select(s.HtmlAttribute).union(select(s.HtmlActionRef)).union(select(s.HtmlColourRef))
    ).exists()


def test_slack_queries_reach_blocks_accessories_options_and_table_cells() -> None:
    option = s.SlackOption(s.SlackText("Ada"), "ada", s.SlackText("Engineer"))
    picker = s.SlackSelect(action=s.SlackActionRef("pick"), options=(option,))
    table = s.SlackTable(((s.SlackText("Name"), s.SlackText("Role")), (s.SlackText("Ada"), s.SlackText("Engineer"))))
    body = s.SlackMessage("Fallback", (s.SlackSection(s.SlackText("Choose"), accessory=picker), table))
    tree = index_tree(body, children=scene_children)

    assert query(tree, select(s.SlackSection).children(select(s.SlackSelect))).one().node is picker
    assert query(tree, select(s.SlackSelect).children(select(s.SlackOption))).one().node is option
    cells = query(tree, select(s.SlackTable).children(select(s.SlackText))).all()
    assert [match.node.content for match in cells] == ["Name", "Role", "Ada", "Engineer"]
    assert cells[0].path.endswith("rows.0.0")
    assert cells[-1].path.endswith("rows.1.1")
    assert not query(tree, select(s.SlackActionRef)).exists()


@pytest.mark.parametrize("modal", [False, True])
def test_slack_view_inputs_and_card_actions_are_queryable(modal: bool) -> None:
    field = s.SlackInput("name", s.SlackText("Name"), s.SlackTextInput("value"))
    button = s.SlackButton(s.SlackText("Open"), url="https://example.test")
    cards = s.SlackCarousel((s.SlackCard(s.SlackText("Card"), actions=(button,)),))
    blocks = (field, cards)
    body = (
        s.SlackModalView("form", s.SlackText("Title"), s.SlackText("Save"), s.SlackText("Close"), blocks)
        if modal
        else s.SlackHomeView(blocks)
    )
    tree = index_tree(body, children=scene_children)

    assert query(tree, select(s.SlackInput).children(select(s.SlackTextInput))).one().node.action_id == "value"
    assert query(tree, select(s.SlackCard).children(select(s.SlackButton))).one().node is button


def test_scene_adapter_keeps_extension_payload_and_unrelated_records_opaque() -> None:
    @dataclass
    class Record:
        children: tuple[s.Text, ...]

    hidden = s.Text("Hidden")
    extension = s.Extension("opaque", 1, {"content": "not a text node"})
    tree = index_tree((Record((hidden,)), extension), children=scene_children)

    assert not query(tree, select(s.Text)).exists()
    assert is_scene_node(extension)
    assert not is_scene_node(Record((hidden,)))


def test_scene_adapter_does_not_reconstruct_nodes_and_preserves_occurrences() -> None:
    constructions: list[None] = []

    @dataclass(frozen=True)
    class Panel(s.Panel):
        def __post_init__(self) -> None:
            constructions.append(None)

    text = s.Text("Repeated")
    panel = Panel((text, text))
    tree = index_tree(panel, children=scene_children)

    found = query(tree, select(s.Text)).all()
    assert len(found) == 2
    assert found[0].node is found[1].node
    assert found[0].path != found[1].path
    assert constructions == [None]
