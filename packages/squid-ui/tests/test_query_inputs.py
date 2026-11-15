"""Convenient tree capture remains explicit about rendering and occurrence lifetimes."""

import pytest

import squid_ui as ui
from squid_ui import scene
from squid_ui import semantic as s
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.selectors import query, select, snapshot
from squid_ui.testing import text_component


def test_query_accepts_nodes_sequences_and_component_trees() -> None:
    paragraph = ui.paragraph("Ready")
    assert query(paragraph, 'Paragraph[text="Ready"]').one().node is paragraph
    assert query([paragraph, paragraph], select(s.Paragraph)).count() == 2
    component = text_component("Ready")
    rendered = render_component_tree(component)
    assert query(rendered, select(component)).one().node is component
    assert query(rendered, 'Paragraph[text="Ready"]').count() == 1
    with pytest.raises(ValueError, match="ComponentTree"):
        query(component, select(s.Paragraph))


def test_explicit_snapshot_preserves_order_and_scope_across_queries() -> None:
    paragraph = ui.paragraph("Ready")
    tree = snapshot(ui.stack(paragraph, paragraph))
    assert snapshot(tree) is tree
    parent = query(tree, select(s.Stack)).one()
    first, second = query(tree, select(s.Paragraph), within=parent).all()
    assert parent.precedes(first)
    assert first.precedes(second)
    assert not second.precedes(first)
    assert not first.precedes(first)
    with pytest.raises(ValueError, match="same snapshot"):
        first.precedes(query(paragraph, select(s.Paragraph)).one())


def test_query_accepts_an_explicit_adapter_without_mutating_nodes() -> None:
    tree = {"child": ui.paragraph("Ready")}

    def children(node: object) -> tuple[tuple[str, object], ...]:
        return tuple(node.items()) if isinstance(node, dict) else ()

    assert query(tree, "Paragraph", children=children).one().node is tree["child"]
    captured = snapshot(tree, children=children)
    with pytest.raises(ValueError, match="existing snapshot"):
        query(captured, "Paragraph", children=children)


def test_query_adapts_planned_scenes_without_an_explicit_snapshot() -> None:
    button = scene.Button("Save", "save")
    planned = scene.Scene(1, "discord", 1, scene.ComponentsV2((scene.Row((button,)),)))
    assert query(planned, select(scene.Row).children(select(scene.Button))).one().node is button
    assert query(planned, 'scene.Button[label="Save"]').one().node is button
