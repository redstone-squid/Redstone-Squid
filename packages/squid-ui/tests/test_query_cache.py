"""Render-local structural reuse never caches mutable predicate results."""

import gc
import weakref
from abc import ABCMeta
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import fields, replace
from typing import override

import pytest

import squid_ui as ui
from squid_i18n import Localization, source_text, tr
from squid_ui import scene, selectors
from squid_ui.authoring import semantic as s
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.scene.codec import Codec
from squid_ui.selectors import TreeIndex, index_tree, query, select, ui_children
from squid_ui.testing import walk


class Editor(ui.Component):
    enabled: bool = ui.state(True)

    @override
    def render(self) -> ui.LayoutNode:
        return ui.paragraph(tr("cache-greeting = Hello"), key="greeting")


class Screen(ui.Component):
    def __init__(self) -> None:
        self.editor = Editor()

    @override
    def render(self) -> ui.LayoutNode:
        return ui.stack(self.editor.keyed("left"), self.editor.keyed("right"))


def test_render_queries_share_placement_scope_and_order_without_explicit_index() -> None:
    screen = Screen()
    rendered = render_component_tree(screen)
    left = query(rendered, select(screen.editor).key("left")).one()
    right = query(rendered, select(screen.editor).key("right")).one()
    assert left.precedes(right)
    assert query(rendered, 'Paragraph[key="greeting"]', within=left).one().owner == "left"
    assert index_tree(rendered) is index_tree(rendered)
    subsequent = render_component_tree(screen)
    assert index_tree(subsequent) is not index_tree(rendered)
    with pytest.raises(ValueError, match="different tree index"):
        query(subsequent, "Paragraph", within=left)


def test_render_cache_never_reuses_predicate_or_localization_results() -> None:
    editor = Editor()
    rendered = render_component_tree(editor)
    enabled = select(editor).attr("enabled", value=True)
    assert query(rendered, enabled).exists()
    editor.enabled = False
    assert not query(rendered, enabled).exists()
    allow = True
    predicate = select(editor).where(lambda _: allow)
    assert query(rendered, predicate).exists()
    allow = False
    assert not query(rendered, predicate).exists()
    french = Localization("fr", ("cache-greeting = Bonjour",))
    assert query(rendered, 'Paragraph[text="Hello"]').exists()
    assert not query(rendered, 'Paragraph[text="Hello"]', localization=french).exists()
    assert query(rendered, 'Paragraph[text="Bonjour"]', localization=french).exists()


def test_scene_index_does_not_change_serialization_equality_or_generic_walks() -> None:
    button = discord_scene.Button("Save", "save")
    planned = scene.Scene(1, "discord", 1, discord_scene.ComponentsV2((discord_scene.Row((button,)),)))
    equal = replace(planned)
    encoded = Codec.dumps(planned)
    fingerprint = Codec.fingerprint(planned)
    walked = tuple(walk(planned))
    assert query(planned, 'scene.Button[label="Save"]').one().node is button
    assert query(planned, select(discord_scene.Row)).one().precedes(query(planned, select(discord_scene.Button)).one())
    assert tuple(walk(planned)) == walked
    assert Codec.dumps(planned) == encoded
    assert Codec.fingerprint(planned) == fingerprint
    assert planned == equal
    assert "_query_index" not in {entry.name for entry in fields(planned)}
    for cloned in (copy(planned), replace(planned)):
        assert index_tree(cloned) is not index_tree(planned)
        assert query(cloned, select(cloned)).one().node is cloned
        assert not query(cloned, select(planned)).exists()


def test_component_tree_copies_start_with_their_own_index() -> None:
    rendered = render_component_tree(Editor())
    indexed = index_tree(rendered)
    for cloned in (copy(rendered), replace(rendered)):
        assert index_tree(cloned) is not indexed
    assert "_query_index" not in {entry.name for entry in fields(rendered)}


def test_custom_adapters_and_mutable_inputs_are_indexed_afresh() -> None:
    values = [ui.paragraph("one")]
    assert query(values, "Paragraph").count() == 1
    values.append(ui.paragraph("two"))
    assert query(values, "Paragraph").count() == 2
    assert index_tree(values) is not index_tree(values)
    rendered = render_component_tree(Editor())
    normal = index_tree(rendered)

    def custom(node: object) -> Iterable[tuple[str, object]]:
        return ui_children(node)

    assert index_tree(rendered, children=custom) is not normal
    assert index_tree(rendered, children=custom) is not index_tree(rendered, children=custom)
    assert index_tree(rendered) is normal


def test_concurrent_queries_share_one_render_index() -> None:
    rendered = render_component_tree(Editor())
    with ThreadPoolExecutor(max_workers=4) as pool:
        indexes = tuple(pool.map(index_tree, [rendered] * 12))
    assert all(indexed is indexes[0] for indexed in indexes)


def test_type_candidates_include_subclasses_and_dynamic_abc_checks_still_run() -> None:
    class Paragraph(s.Paragraph):
        pass

    class Marker(metaclass=ABCMeta):  # noqa: B024 - virtual subclass registration exercises dynamic isinstance.
        pass

    node = Paragraph("hello")
    tree = index_tree(node)
    assert query(tree, select(s.Paragraph)).one().node is node
    assert not query(tree, select(Marker)).exists()
    Marker.register(Paragraph)
    assert query(tree, select(Marker)).one().node is node


def test_cached_render_can_be_collected_without_a_global_owner_cache() -> None:
    component = Editor()
    reference = weakref.ref(component)
    rendered = render_component_tree(component)
    query(rendered, "Paragraph")
    del rendered, component
    gc.collect()
    assert reference() is None


def test_type_and_key_indexes_narrow_candidates_in_document_order() -> None:
    tree = index_tree(ui.stack(*(ui.paragraph(str(i), key=f"p{i}") for i in range(100))))
    assert list(tree.candidates(s.Paragraph, ("key", "p50"), 0, len(tree.matches))) == [51]
    assert source_text(query(tree, select(s.Paragraph).key("p50")).one().node.content) == "50"
    assert query(tree, select(s.Paragraph).key("p50").union(select(s.Paragraph).key("p2"))).all()[0].key == "p2"
    assert isinstance(tree, TreeIndex)


def test_render_placement_mappings_cannot_invalidate_a_cached_index() -> None:
    rendered = render_component_tree(Editor())
    index_tree(rendered)
    with pytest.raises(TypeError):
        rendered.components["$"] = Editor()  # type: ignore  # The read-only mapping rejects topology edits.
    with pytest.raises(TypeError):
        rendered.authored_nodes["$"] = ()  # type: ignore  # Authored edges share the same render lifetime.


def test_selector_api_has_no_snapshot_aliases() -> None:
    assert not hasattr(selectors, "Snapshot")
    assert not hasattr(selectors, "snapshot")
