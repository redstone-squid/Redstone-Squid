"""Queries preserve UI occurrence identity and cannot escape a scoped subtree."""

import re
from dataclasses import dataclass
from typing import assert_type, override

import pytest

import squid_ui as ui
from squid_i18n import tr
from squid_ui.authoring import semantic as s
from squid_ui.runtime import ComponentRuntime
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.selectors import Match, QueryError, index_tree, query, scope, select
from squid_ui.testing import render_index


async def pressed(event: ui.PressEvent) -> None: ...


def toolbar(*keys: str) -> s.ActionControls:
    return s.ActionControls(tuple(s.ActionControl(key, key.title(), pressed) for key in keys), key="toolbar")


def test_repeated_objects_are_occurrences_and_union_is_in_tree_order() -> None:
    shared = toolbar("save")
    tree = index_tree(ui.stack(shared, shared))
    controls = select(s.ActionControl)
    found = query(tree, controls.union(controls.key("save"))).all()
    assert len(found) == 2
    assert found[0].node is found[1].node
    assert found[0].path != found[1].path
    assert_type(query(tree, controls).all()[0], Match[s.ActionControl])


def test_relations_and_scope_do_not_escape_the_subtree() -> None:
    tree = index_tree(ui.stack(ui.section(ui.heading("Editor"), toolbar("save")), toolbar("cancel")))
    section = query(tree, select(s.Section)).one()
    save = select(s.ActionControl).key("save")
    assert query(tree, select(s.Stack).descendants(save)).exists()
    assert not query(tree, select(s.Stack).children(save)).exists()
    assert query(tree, select(s.ActionControls).children(save)).exists()
    assert not query(tree, select(s.Stack).descendants(save), within=section).exists()
    assert query(tree, scope().descendants(save), within=section).count() == 1
    assert not query(tree, select(s.Section), within=section).exists()
    assert query(tree, scope(), within=section, include_scope=True).one() == section
    assert not query(tree, select(s.Section).has(select(s.Stack).descendants(save))).exists()
    assert query(tree, select(s.Section).has(save)).one() == section
    assert not query(tree, select(s.Section).has(save, direct=True)).exists()
    assert query(tree, select(s.Section).has(select(s.ActionControls), direct=True)).exists()
    with pytest.raises(ValueError, match="different tree index"):
        query(index_tree(toolbar("save")), save, within=section)


def test_predicates_text_and_missing_attributes() -> None:
    control = s.ActionControl("save", tr(t"selector-save = Save {(name := 'Ada')}"), pressed, available=False)
    tree = index_tree(s.ActionControls((control,), key="toolbar"))
    controls = select(s.ActionControl)
    assert query(tree, controls.attr("available", value=False)).one().node is control
    assert query(tree, controls.label("Save Ada")).exists()
    assert query(tree, controls.label("Ada", contains=True)).exists()
    assert query(tree, controls.label(re.compile(r"Save \w+"))).exists()
    assert not query(tree, controls.label("Ada")).exists()
    assert not query(tree, controls.text("Save Ada")).exists()
    assert not query(tree, controls.attr("missing", None)).exists()
    assert not query(tree, controls.attr("missing", value=False)).exists()
    assert query(tree, controls.has_attr("available").where(lambda node: not node.available)).exists()
    assert query(tree, controls.intersect(select(object).key("save"))).exists()
    assert not query(tree, controls.excluding(controls.key("save"))).exists()


def test_query_cardinality_has_useful_diagnostics() -> None:
    tree = index_tree(toolbar("save", "save"))
    save = select(s.ActionControl).key("save")
    assert query(tree, save).count() == 2
    with pytest.raises(QueryError, match=r"found 2.*") as error:
        query(tree, save).one()
    assert "$/0/items.0" in str(error.value)
    assert "$/0/items.1" in str(error.value)
    with pytest.raises(QueryError, match="zero or one"):
        query(tree, save).optional()
    missing = query(tree, save.key("missing"))
    assert missing.optional() is None
    with pytest.raises(QueryError, match="found 0") as error:
        missing.one()
    assert "key='save'" in str(error.value)


class Editor(ui.Component):
    count: int = ui.state(0)

    @override
    def render(self) -> ui.LayoutNode:
        return ui.stack(ui.paragraph(str(self.count)), toolbar("save"))


class Screen(ui.Component):
    shown: bool = ui.state(True)

    def __init__(self) -> None:
        self.editor = Editor()

    @override
    def render(self) -> ui.LayoutNode:
        if self.shown:
            return ui.stack(self.editor.keyed("primary"), self.editor.keyed("preview"))
        return ui.paragraph("closed")


def test_component_placements_keep_local_keys_and_owner_paths() -> None:
    component = Screen()
    rendered = render_component_tree(component)
    tree = index_tree(rendered)
    editors = query(tree, select(Editor)).all()
    assert [match.key for match in editors] == ["primary", "preview"]
    assert editors[0].node is editors[1].node
    save = select(s.ActionControl).key("save")
    assert query(tree, save).count() == 2
    primary = query(tree, save, within=editors[0]).one()
    assert primary.owner == "primary"
    assert primary.qualified_key == "primary.save"
    assert query(tree, save.qualified_key("primary.save")).one() == primary
    assert query(tree, select(Editor).key("preview").descendants(save)).one().owner == "preview"
    with pytest.raises(ValueError, match="ComponentTree"):
        index_tree(component)
    # Expanded-node inspection is explicit and cannot recover local keys or owners.
    assert not query(index_tree(rendered.nodes), save).exists()
    assert query(index_tree(rendered.nodes), select(s.ActionControl).key("primary.save")).exists()


@pytest.mark.parametrize("incremental", [True, False])
def test_runtime_snapshots_survive_reuse_and_removed_placements(incremental: bool) -> None:
    root = Screen()
    runtime = ComponentRuntime(root)
    initial = runtime.render()
    runtime.commit(initial)
    root.editor.count = 1
    updated = runtime.render(reuse_committed=incremental)
    runtime.commit(updated)
    assert query(index_tree(updated), select(s.Paragraph).text("1")).count() == 2
    assert query(index_tree(initial), select(s.Paragraph).text("0")).count() == 2
    root.shown = False
    closed = runtime.render(reuse_committed=incremental)
    assert not query(index_tree(closed), select(Editor)).exists()
    assert query(index_tree(initial), select(Editor)).count() == 2
    runtime.finish()


def test_explicit_adapters_handle_cycles_without_deduplicating_siblings() -> None:
    @dataclass
    class Node:
        children: list[object]

    child = Node([])
    root = Node([child, child])

    def edges(value: object) -> list[tuple[str, object]]:
        assert isinstance(value, Node)
        return [(str(i), item) for i, item in enumerate(value.children)]

    assert query(index_tree(root, children=edges), select(Node)).count() == 3
    root.children.append(root)
    with pytest.raises(ValueError, match="cycle"):
        index_tree(root, children=edges)


def test_ui_traversal_does_not_follow_application_records() -> None:
    @dataclass
    class ApplicationRecord:
        nested: s.ActionControls

    assert not query(index_tree(ApplicationRecord(toolbar("private"))), select(s.ActionControl)).exists()


def test_incomplete_renders_are_not_silently_queried() -> None:
    root = Screen()
    tree = render_component_tree(root, defer=lambda _component: True)
    with pytest.raises(ValueError, match="incomplete"):
        index_tree(tree)


def test_testing_snapshot_helper_preserves_component_scope() -> None:
    tree = render_index(Screen())
    editor = query(tree, select(Editor).key("primary")).one()
    assert query(tree, select(s.ActionControl).key("save"), within=editor).one().owner == "primary"


def test_authored_alternatives_and_choice_values_are_queryable() -> None:
    alternate = s.FallbackContent(toolbar("full"), (toolbar("compact"),))
    choices = s.Choices("pick", (s.Choice("ada", "Ada"),))
    tree = index_tree(ui.stack(alternate, choices))
    assert [match.key for match in query(tree, select(s.ActionControl)).all()] == ["full", "compact"]
    assert query(tree, select(s.Choices).children(select(s.Choice).label("Ada"))).one().key == "ada"


def test_inspection_does_not_reconstruct_nodes_or_render_components() -> None:
    constructions: list[str] = []

    @dataclass(frozen=True)
    class Stack(s.Stack):
        def __post_init__(self) -> None:
            constructions.append("constructed")

    node = Stack((toolbar("save"),))
    assert query(index_tree(node), select(s.ActionControl)).count() == 1
    assert constructions == ["constructed"]


def test_instance_selection_matches_identity_not_equality() -> None:
    first = ui.paragraph("equal")
    second = ui.paragraph("equal")
    assert first == second and first is not second
    tree = index_tree(ui.stack(first, second, first))
    found = query(tree, select(first)).all()
    assert len(found) == 2
    assert all(match.node is first for match in found)
    assert_type(found[0], Match[s.Paragraph])
    assert query(tree, select(s.Paragraph)).count() == 3
    assert not query(tree, select(ui.paragraph("equal"))).exists()


def test_instance_selection_can_narrow_a_component_placement() -> None:
    screen = Screen()
    tree = render_index(screen)
    editor = select(screen.editor)
    assert_type(query(tree, editor).all()[0], Match[Editor])
    assert query(tree, editor).count() == 2
    primary = query(tree, editor.qualified_key("primary")).one()
    assert primary.node is screen.editor
    assert query(tree, editor, within=primary, include_scope=True).one() == primary
    assert query(tree, select(ui.Component)).count() == 3


def test_string_query_can_select_custom_component_placements() -> None:
    screen = Screen()
    tree = render_index(screen)
    primary = query(tree, 'Editor[key="primary"]', types={"Editor": Editor}).one()
    assert_type(primary, Match[object])
    assert primary.node is screen.editor
    assert query(tree, 'ActionControl[key="save"]', within=primary).one().owner == "primary"
