"""Presentation choices preserve references independently of shape and preference."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.semantics import TableCell, check_alternative, semantic_graph
from squid_ui.presentations import (
    alternative,
    alternatives,
    change_interaction,
    corresponds,
    equivalent,
    losses,
    omit,
    ref,
)


def test_reordered_choices_share_meaning_without_a_baseline() -> None:
    width = sl.paragraph("Width: 3", key="width")
    edition = sl.paragraph("Edition: Java", key="edition")
    content = sl.stack(width, edition, key="source")
    full = alternative(content, key="full", fidelity=equivalent(), preference=10)
    compact = alternative(sl.stack(edition, width, key="compact"), key="compact", fidelity=equivalent())
    group = alternatives(compact, full, content=content, key="summary")
    for choice in group.choices:
        checked = check_alternative(group, choice)
        assert frozenset(unit for covered in checked.coverage.values() for unit in covered) == checked.source.leaves
        assert checked.omitted == frozenset()
    assert check_alternative(group, compact) == check_alternative(replace(group, choices=(full, compact)), compact)


def test_known_omission_cannot_claim_equivalence() -> None:
    width = sl.paragraph("3", key="width")
    content = sl.stack(width, sl.paragraph("Java", key="edition"))
    compact = alternative(width, key="compact", fidelity=equivalent())
    group = alternatives(compact, content=content, key="summary")
    with pytest.raises(LayoutInvariantError, match="omissions"):
        check_alternative(group, compact)
    declared = replace(compact, fidelity=losses(omit("edition")))
    checked = check_alternative(group, declared)
    assert checked.omitted == frozenset({ref("@0", "edition")})
    with pytest.raises(LayoutInvariantError, match="omissions"):
        check_alternative(group, replace(compact, fidelity=losses(omit("width"))))


def test_combined_and_split_presentations_declare_correspondence() -> None:
    content = sl.stack(sl.paragraph("3 wide", key="width"), sl.paragraph("Java", key="edition"))
    combined = alternative(
        sl.paragraph("Java, 3 wide", key="summary"),
        key="combined",
        fidelity=equivalent(),
        correspondence=(corresponds("summary", "width", "edition"),),
    )
    group = alternatives(combined, content=content, key="build")
    assert check_alternative(group, combined).coverage[ref("summary")] == semantic_graph(content).leaves
    split = alternative(
        sl.stack(sl.paragraph("3", key="number"), sl.paragraph("wide", key="unit")),
        key="split",
        fidelity=equivalent(),
        correspondence=(corresponds("number", "width"), corresponds("unit", "width")),
    )
    split_group = alternatives(split, content=sl.paragraph("3 wide", key="width"), key="dimension")
    assert set(check_alternative(split_group, split).coverage.values()) == {frozenset({ref("width")})}


def test_ambiguous_short_reference_requires_its_scope() -> None:
    source = sl.stack(
        sl.stack(sl.paragraph("3", key="size"), key="left"),
        sl.stack(sl.paragraph("5", key="size"), key="right"),
    )
    graph = semantic_graph(source)
    with pytest.raises(LayoutInvariantError, match="ambiguous"):
        graph.resolve(ref("size"))
    with pytest.raises(LayoutInvariantError, match="unknown"):
        graph.resolve(ref("missing"))
    assert graph.resolve(ref("left", "size")) == ref("@0", "left", "size")


def test_duplicate_semantic_and_choice_keys_are_rejected() -> None:
    node = sl.paragraph("x", key="x")
    with pytest.raises(LayoutInvariantError, match="duplicate semantic"):
        semantic_graph(sl.stack(node, node))
    choice = alternative(node, key="same", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="alternative keys"):
        alternatives(choice, choice, content=node, key="duplicate")


def test_table_cells_keep_row_and_column_identity() -> None:
    table = sl.table(
        sl.columns(sl.column("Width", key="width"), sl.column("Edition", key="edition")),
        sl.table_row("3", "Java", key="build-a"),
        sl.table_row("5", "Bedrock", key="build-b"),
        key="builds",
    )
    graph = semantic_graph(table)
    assert len(graph.leaves) == 6
    cell = graph.units[ref("builds", "rows", "build-b", "width")]
    assert isinstance(cell.value, TableCell)
    assert (cell.value.row, cell.value.column) == ("build-b", "width")
    assert cell.parent == ref("builds", "rows", "build-b")
    choice = alternative(table, key="table", fidelity=equivalent())
    assert not check_alternative(alternatives(choice, content=table, key="data"), choice).omitted


def test_callback_changes_require_specific_declarations_without_invocation() -> None:
    async def original(event: sl.PressEvent[object]) -> None:
        raise AssertionError("planning must not invoke handlers")

    async def replacement(event: sl.PressEvent[object]) -> None:
        raise AssertionError("planning must not invoke handlers")

    button = sl.action_control("Open", original, key="open")
    content = sl.action_controls(button, key="actions")
    changed = sl.action_controls(replace(button, on_trigger=replacement), key="actions")
    choice = alternative(changed, key="changed", fidelity=equivalent())
    group = alternatives(choice, content=content, key="toolbar")
    with pytest.raises(LayoutInvariantError, match="undeclared interaction"):
        check_alternative(group, choice)
    permitted = replace(choice, fidelity=losses(change_interaction("open", attribute="on_trigger")))
    assert check_alternative(group, permitted).coverage
    with pytest.raises(LayoutInvariantError, match="unknown interaction attribute"):
        check_alternative(group, replace(choice, fidelity=losses(change_interaction("open", attribute="typo"))))


def test_changed_link_destination_is_behavior() -> None:
    content = sl.action_controls(sl.link("Docs", "https://example.com/old", key="docs"), key="links")
    changed = sl.action_controls(sl.link("Docs", "https://example.com/new", key="docs"), key="links")
    choice = alternative(changed, key="new", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="undeclared interaction"):
        check_alternative(alternatives(choice, content=content, key="toolbar"), choice)


def test_unknown_nodes_are_not_inferred_from_attributes() -> None:
    class Lookalike(sl.Renderable):
        key = "looks-supported"
        children = (sl.paragraph("secret"),)

    with pytest.raises(LayoutInvariantError, match="does not yet support Lookalike"):
        semantic_graph(Lookalike())


def test_layout_constraints_survive_semantic_indexing() -> None:
    content = sl.unbreakable(sl.paragraph("Keep together", key="body"))
    graph = semantic_graph(content)
    assert graph.units[ref("@0")].value is content
    assert graph.units[ref("@0", "body")].parent == ref("@0")


def test_toggle_ownership_and_availability_are_behavior() -> None:
    content = sl.toggle("Enabled", key="enabled")
    choice = alternative(replace(content, available=False), key="disabled", fidelity=equivalent())
    group = alternatives(choice, content=content, key="setting")
    with pytest.raises(LayoutInvariantError, match="undeclared interaction"):
        check_alternative(group, choice)
    changed = replace(choice, node=replace(content, on=sl.uncontrolled(initial=True)))
    with pytest.raises(LayoutInvariantError, match="undeclared interaction"):
        check_alternative(group, changed)


def test_unkeyed_table_columns_have_positional_identity() -> None:
    table = sl.table(sl.columns(sl.column("A"), sl.column("B")), sl.table_row("1", "2"), key="t")
    graph = semantic_graph(table)
    assert ref("t", "rows", "@0", "@1") in graph.leaves
    assert len(graph.leaves) == 4
