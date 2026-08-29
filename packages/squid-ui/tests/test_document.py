"""Normalizing a render result into the document the planners are handed."""

import pytest

import squid_ui as sl
from squid_ui.document import Asset, Document, InlineAsset, as_document
from squid_ui.errors import LayoutInvariantError


def test_a_document_passes_through_with_its_assets():
    document = Document((sl.paragraph("body"),), assets=(), key="k")
    assert as_document(document) is document


def test_one_node_becomes_a_single_child():
    assert as_document(sl.paragraph("body")).children == (sl.paragraph("body"),)


def test_a_sequence_becomes_the_children_in_order():
    nodes = [sl.paragraph("first"), sl.paragraph("second")]
    assert as_document(nodes).children == tuple(nodes)


def test_returned_text_is_refused_rather_than_taken_apart():
    """`str` is a `Sequence`, so the sequence branch would plan one node per character."""
    with pytest.raises(LayoutInvariantError, match="returned text"):
        as_document("body")  # type: ignore[arg-type]


def test_returned_bytes_are_refused_for_the_same_reason():
    with pytest.raises(LayoutInvariantError, match="returned text"):
        as_document(b"body")  # type: ignore[arg-type]


def test_an_empty_sequence_is_an_empty_document():
    assert as_document([]).children == ()
    assert as_document([]).assets == ()


def test_assets_survive_a_document_passed_through():
    asset = Asset(key="k", name="x.png", media_type="image/png", source=InlineAsset(b"x"))
    document = Document((sl.paragraph("body"),), assets=(asset,))
    assert as_document(document).assets == (asset,)
