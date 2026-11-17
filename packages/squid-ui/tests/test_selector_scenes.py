"""Planned scenes expose display relationships while keeping binding metadata outside the tree."""

from dataclasses import dataclass

import pytest

from squid_ui import scene as s
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.html import scene as html_scene
from squid_ui.ext.slack import scene as slack_scene
from squid_ui.selectors import index_tree, query, select
from squid_ui.selectors._scene import is_scene_node, scene_children


def test_discord_scene_queries_reach_nested_controls_and_options() -> None:
    save = discord_scene.Button("Save", "save")
    body = discord_scene.ComponentsV2(
        (
            discord_scene.Panel(
                (
                    discord_scene.Section((discord_scene.Text("Summary"),), save),
                    discord_scene.Select((discord_scene.Option("Ada", "ada"),), "pick"),
                    discord_scene.Gallery((discord_scene.GalleryItem("https://example.test/image.png"),)),
                )
            ),
        )
    )
    scene = s.Scene(
        1, "discord", 1, body, pagers=(s.Pager(key="pages", page=0, pages=2, content_fingerprint="fingerprint"),)
    )
    tree = index_tree(scene, children=scene_children)

    assert query(tree, select(discord_scene.Section).children(select(discord_scene.Button))).one().node is save
    assert (
        query(tree, select(discord_scene.Select).children(select(discord_scene.Option).label("Ada"))).one().node.value
        == "ada"
    )
    assert query(tree, select(discord_scene.GalleryItem)).count() == 1
    assert not query(tree, select(s.Pager)).exists()


def test_classic_scene_queries_preserve_embed_and_row_structure() -> None:
    field = discord_scene.EmbedField(name="Name", value="Ada")
    body = discord_scene.ClassicMessage(
        "Message",
        embeds=(
            discord_scene.Embed(
                fields=(field,),
                author=discord_scene.EmbedAuthor(name="Author"),
                footer=discord_scene.EmbedFooter(text="Footer"),
            ),
        ),
        rows=(discord_scene.ClassicRow((discord_scene.Button("Save", "save"),)),),
    )
    tree = index_tree(body, children=scene_children)

    assert query(tree, select(discord_scene.Embed).children(select(discord_scene.EmbedField))).one().node is field
    assert (
        query(tree, select(discord_scene.ClassicRow).children(select(discord_scene.Button))).one().node.action == "save"
    )
    assert query(tree, select(discord_scene.EmbedAuthor)).count() == 1
    assert query(tree, select(discord_scene.EmbedFooter)).count() == 1


def test_html_scene_queries_visit_elements_and_text_but_not_references() -> None:
    text = html_scene.HtmlText("Save")
    button = html_scene.HtmlElement(
        html_scene.HtmlTag.BUTTON,
        (text,),
        attributes=(html_scene.HtmlAttribute(html_scene.HtmlAttributeName.ID, "save"),),
        action=html_scene.HtmlActionRef("save"),
        colour=html_scene.HtmlColourRef(0x123456),
    )
    body = html_scene.HtmlBody((html_scene.HtmlElement(html_scene.HtmlTag.MAIN, (button,)),))
    tree = index_tree(body, children=scene_children)

    assert (
        query(
            tree,
            select(html_scene.HtmlElement)
            .attr("tag", html_scene.HtmlTag.BUTTON)
            .has(select(html_scene.HtmlText).text("Save")),
        )
        .one()
        .node
        is button
    )
    assert query(tree, select(html_scene.HtmlText)).one().node is text
    assert not query(
        tree,
        select(html_scene.HtmlAttribute)
        .union(select(html_scene.HtmlActionRef))
        .union(select(html_scene.HtmlColourRef)),
    ).exists()


def test_slack_queries_reach_blocks_accessories_options_and_table_cells() -> None:
    option = slack_scene.SlackOption(slack_scene.SlackText("Ada"), "ada", slack_scene.SlackText("Engineer"))
    picker = slack_scene.SlackSelect(action=slack_scene.SlackActionRef("pick"), options=(option,))
    table = slack_scene.SlackTable(
        (
            (slack_scene.SlackText("Name"), slack_scene.SlackText("Role")),
            (slack_scene.SlackText("Ada"), slack_scene.SlackText("Engineer")),
        )
    )
    body = slack_scene.SlackMessage(
        "Fallback", (slack_scene.SlackSection(slack_scene.SlackText("Choose"), accessory=picker), table)
    )
    tree = index_tree(body, children=scene_children)

    assert query(tree, select(slack_scene.SlackSection).children(select(slack_scene.SlackSelect))).one().node is picker
    assert query(tree, select(slack_scene.SlackSelect).children(select(slack_scene.SlackOption))).one().node is option
    cells = query(tree, select(slack_scene.SlackTable).children(select(slack_scene.SlackText))).all()
    assert [match.node.content for match in cells] == ["Name", "Role", "Ada", "Engineer"]
    assert cells[0].path.endswith("rows.0.0")
    assert cells[-1].path.endswith("rows.1.1")
    assert not query(tree, select(slack_scene.SlackActionRef)).exists()


@pytest.mark.parametrize("modal", [False, True])
def test_slack_view_inputs_and_card_actions_are_queryable(modal: bool) -> None:
    field = slack_scene.SlackInput("name", slack_scene.SlackText("Name"), slack_scene.SlackTextInput("value"))
    button = slack_scene.SlackButton(slack_scene.SlackText("Open"), url="https://example.test")
    cards = slack_scene.SlackCarousel((slack_scene.SlackCard(slack_scene.SlackText("Card"), actions=(button,)),))
    blocks = (field, cards)
    body = (
        slack_scene.SlackModalView(
            "form",
            slack_scene.SlackText("Title"),
            slack_scene.SlackText("Save"),
            slack_scene.SlackText("Close"),
            blocks,
        )
        if modal
        else slack_scene.SlackHomeView(blocks)
    )
    tree = index_tree(body, children=scene_children)

    assert (
        query(tree, select(slack_scene.SlackInput).children(select(slack_scene.SlackTextInput))).one().node.action_id
        == "value"
    )
    assert query(tree, select(slack_scene.SlackCard).children(select(slack_scene.SlackButton))).one().node is button


def test_scene_adapter_keeps_extension_payload_and_unrelated_records_opaque() -> None:
    @dataclass
    class Record:
        children: tuple[discord_scene.Text, ...]

    hidden = discord_scene.Text("Hidden")
    extension = discord_scene.Extension("opaque", 1, {"content": "not a text node"})
    tree = index_tree((Record((hidden,)), extension), children=scene_children)

    assert not query(tree, select(discord_scene.Text)).exists()
    assert is_scene_node(extension)
    assert not is_scene_node(Record((hidden,)))


def test_scene_adapter_does_not_reconstruct_nodes_and_preserves_occurrences() -> None:
    constructions: list[None] = []

    @dataclass(frozen=True)
    class Panel(discord_scene.Panel):
        def __post_init__(self) -> None:
            constructions.append(None)

    text = discord_scene.Text("Repeated")
    panel = Panel((text, text))
    tree = index_tree(panel, children=scene_children)

    found = query(tree, select(discord_scene.Text)).all()
    assert len(found) == 2
    assert found[0].node is found[1].node
    assert found[0].path != found[1].path
    assert constructions == [None]
