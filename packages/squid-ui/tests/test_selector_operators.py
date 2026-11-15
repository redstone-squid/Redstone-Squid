"""String attributes and sibling relations respect occurrence scope."""

from typing import assert_type, override

import pytest

import squid_ui as ui
from squid_ui import scene
from squid_ui.authoring import semantic as s
from squid_ui.primitives import Lines
from squid_ui.primitives.constraints import Paginate
from squid_ui.selectors import Match, Selector, SelectorSyntaxError, index_tree, parse, query, scope, select
from squid_ui.testing import render_index


def test_dotted_policy_attributes_select_the_ui_owner() -> None:
    lines = Lines(("one", "two"), overflow=Paginate(key="results"))
    tree = index_tree(lines)
    assert query(tree, select(Lines).attr("overflow.key", "results")).one().node is lines
    assert query(tree, 'Lines[overflow.key="results"]', types={"Lines": Lines}).one().node is lines
    assert query(tree, select(Lines).has_attr("overflow.key")).exists()
    assert not query(tree, select(Lines).attr("overflow.missing", None)).exists()
    assert not query(tree, select(Paginate)).exists()


@pytest.mark.parametrize(
    ("source", "typed"),
    [
        ('Paragraph[key^="editor."]', select(s.Paragraph).attr_starts_with("key", "editor.")),
        ('Paragraph[key$=".save"]', select(s.Paragraph).attr_ends_with("key", ".save")),
        ('Paragraph[key*="tor.sa"]', select(s.Paragraph).attr_contains("key", "tor.sa")),
    ],
)
def test_string_operators_match_only_strings(source: str, typed: Selector[s.Paragraph]) -> None:
    selected = ui.paragraph("save", key="editor.save")
    tree = index_tree((selected, ui.paragraph("unkeyed"), ui.heading("heading")))
    assert query(tree, source).one().node is selected
    assert query(tree, typed).one().node is selected
    assert not query(tree, 'Heading[level^="2"]').exists()
    assert not query(tree, 'Paragraph[missing*=""]').exists()


def test_text_string_operators_resolve_content_and_escape_metacharacters() -> None:
    tree = index_tree((ui.paragraph("[Save] report.txt"), ui.paragraph("Save reportXtxt")))
    assert query(tree, 'Paragraph[text^="[Save]"][text$=".txt"]').count() == 1
    with pytest.raises(SelectorSyntaxError, match="require strings"):
        parse("Paragraph[key$=2]")


def test_component_string_keys_use_placement_metadata() -> None:
    class Editor(ui.Component):
        @override
        def render(self) -> ui.LayoutNode:
            return ui.paragraph("inside")

    class Screen(ui.Component):
        def __init__(self) -> None:
            self.editor = Editor()

        @override
        def render(self) -> ui.LayoutNode:
            return self.editor.keyed("primary-editor")

    tree = render_index(Screen())
    assert query(tree, select(Editor).attr_ends_with("key", "editor")).count() == 1
    assert query(tree, 'Editor[qualified_key^="primary"]', types={"Editor": Editor}).count() == 1


def test_sibling_relations_skip_descendants_and_preserve_types() -> None:
    first = ui.paragraph("first", key="first")
    second = ui.stack(ui.paragraph("nested"))
    third = ui.paragraph("third", key="third")
    tree = index_tree(ui.stack(first, second, third))
    following = query(tree, select(first).following_siblings(select(s.Paragraph))).one()
    assert_type(following, Match[s.Paragraph])
    assert following.node is third
    assert not query(tree, select(first).next_sibling(select(s.Paragraph))).exists()
    assert query(tree, select(first).next_sibling(select(s.Stack))).one().node is second
    assert query(tree, 'Paragraph[key="first"] ~ Paragraph').one().node is third
    assert query(tree, 'Paragraph[key="first"] + Stack').one().node is second
    assert query(tree, "Stack + Paragraph").one().node is third


def test_siblings_cannot_escape_query_scope() -> None:
    first = ui.paragraph("first", key="first")
    second = ui.stack(ui.paragraph("second", key="second"))
    third = ui.paragraph("third", key="third")
    tree = index_tree((first, second, third))
    within = query(tree, select(second)).one()
    assert not query(tree, "Paragraph + Stack", within=within, include_scope=True).exists()
    assert not query(tree, "Stack:has(+ Paragraph)", within=within, include_scope=True).exists()
    assert not query(tree, scope().following_siblings(select(s.Paragraph)), within=within).exists()


def test_relative_has_siblings_and_descendant_branches_stay_separate() -> None:
    first = ui.paragraph("first", key="first")
    second = ui.stack(ui.paragraph("nested", key="nested"))
    third = ui.paragraph("third", key="third")
    tree = index_tree((first, second, third))
    assert query(tree, 'Paragraph:has(+ Stack > Paragraph[key="nested"])').one().node is first
    assert query(tree, 'Paragraph:has(~ Paragraph[key="third"])').one().node is first
    assert not query(tree, "Paragraph:has(+ Paragraph)").exists()
    assert query(tree, "Stack:has(> Paragraph, + Paragraph)").one().node is second
    # A descendant branch must not accidentally search the sibling range of another branch.
    assert not query(tree, 'Paragraph:has(+ Heading, Paragraph[key="nested"])').exists()


def test_qualified_scene_types_are_registered_without_shadowing_semantics() -> None:
    text = scene.Text("planned")
    paragraph = ui.paragraph("authored")
    tree = index_tree((text, paragraph))
    assert query(tree, "scene.Text").one().node is text
    assert query(tree, "Paragraph").one().node is paragraph


def test_missing_metadata_is_not_a_present_or_null_attribute() -> None:
    tree = index_tree(ui.paragraph("unkeyed"))
    assert not query(tree, select(s.Paragraph).has_attr("key")).exists()
    assert not query(tree, select(s.Paragraph).attr("qualified_key", None)).exists()
