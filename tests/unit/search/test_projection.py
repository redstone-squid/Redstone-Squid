"""Tests for search projection normalization and typed facets."""

from decimal import Decimal

from whenever import Instant

from squid.search.infrastructure.projection import (
    ProjectionFacet,
    SearchProjection,
    build_facet_models,
    normalize_search_text,
    projection_source_hash,
)


def test_normalize_collapses_case_and_whitespace() -> None:
    assert normalize_search_text("  Three   BY   Three ") == "three by three"


def test_source_hash_changes_with_searchable_content() -> None:
    original = SearchProjection(resource_kind="build", source_key="1", title="3x3 Door")
    edited = SearchProjection(resource_kind="build", source_key="1", title="Fast 3x3 Door")

    assert projection_source_hash(original) == projection_source_hash(original)
    assert projection_source_hash(original) != projection_source_hash(edited)


def test_facet_models_assign_per_field_ordinals_and_typed_columns() -> None:
    completion = Instant.parse_iso("2026-07-30T12:00:00Z")
    models = build_facet_models(
        42,
        (
            ProjectionFacet("tag", "slimeless"),
            ProjectionFacet("tag", "seamless"),
            ProjectionFacet("volume", Decimal(27)),
            ProjectionFacet("completion_at", completion),
            ProjectionFacet("verified", value=True),
        ),
    )

    assert [(model.field_name, model.ordinal) for model in models] == [
        ("tag", 0),
        ("tag", 1),
        ("volume", 0),
        ("completion_at", 0),
        ("verified", 0),
    ]
    assert models[0].text_value == "slimeless"
    assert models[2].numeric_value == Decimal(27)
    assert models[3].timestamp_value == completion
    assert models[4].boolean_value is True
