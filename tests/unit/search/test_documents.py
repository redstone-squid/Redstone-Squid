"""Tests for the rows and vectors derived from a projection."""

from dataclasses import replace
from decimal import Decimal

from squid.core.search.domain import FreeTextWeight
from squid.core.search.infrastructure.documents import (
    FieldValue,
    SearchProjection,
    index_document,
    tsvector_literal,
)

_RECORD = SearchProjection(
    resource_kind="record",
    source_key="record:1",
    title="Fastest 2x2 Glass Door",
    subtitle="No Entities",
    values=(
        FieldValue("title", "Fastest 2x2 Glass Door"),
        FieldValue("subtitle", "No Entities"),
        FieldValue("restriction", "Entityless"),
        FieldValue("holder_title", "No Furnaces Door", 8329),
        FieldValue("volume", Decimal(27), 8329),
    ),
)


def test_bare_text_weights_category_fields_above_holder_fields() -> None:
    vector = index_document(_RECORD).search_vector

    assert "'glass':3A" in vector
    assert "'entities':7B" in vector
    assert "'entityless':9B" in vector
    assert "'furnaces':12D" in vector


def test_values_are_separated_so_phrases_stay_inside_one_value() -> None:
    assert tsvector_literal(((("glass", "door"), None), (("door", "frame"), FreeTextWeight.A))) == (
        "'glass':1 'door':2,4A 'frame':5A"
    )


def test_text_values_keep_their_original_and_normalized_forms() -> None:
    indexed = index_document(_RECORD)
    holder = next(value for value in indexed.values if value.field == "holder_title")

    assert holder.text == "No Furnaces Door"
    assert holder.normalized == "no furnaces door"
    assert holder.holder_build_id == 8329
    assert holder.term_vector == "'no':1 'furnaces':2 'door':3"
    assert next(value for value in indexed.values if value.field == "volume").numeric == Decimal(27)
    assert indexed.heading_terms == ("2x2", "door", "entities", "fastest", "glass", "no")
    assert {"glass", "entityless", "furnaces"} <= indexed.terms


def test_embedding_text_leaves_out_holders() -> None:
    assert index_document(_RECORD).embedding_text == "Fastest 2x2 Glass Door\nNo Entities\nEntityless"


def test_a_holder_change_rewrites_the_document_but_keeps_the_embedding() -> None:
    changed = replace(_RECORD, values=(*_RECORD.values[:3], FieldValue("holder_title", "Other Door", 9047)))

    before, after = index_document(_RECORD), index_document(changed)

    assert before.source_hash != after.source_hash
    assert before.embedding_hash == after.embedding_hash
