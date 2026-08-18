"""Tests for search projection normalization and typed facets."""

from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.search.infrastructure.projection import (
    ProjectionFacet,
    SearchProjection,
    SearchProjectionLoader,
    build_facet_models,
    normalize_search_text,
    projection_source_hash,
)


@pytest.mark.asyncio
async def test_loader_rejects_retired_legacy_record_keys() -> None:
    session = AsyncMock(spec=AsyncSession)
    loader = SearchProjectionLoader(cast(AsyncSession, session))

    assert await loader.load("record", "legacy-smallest:1") is None
    session.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_documents_carry_titles_not_category_keys() -> None:
    definition = SimpleNamespace(
        id=7,
        record_class="fastest",
        build_kind="door",
        version_scope="all_time",
        category_key="door:door|2x2|t[20]|Door:r[]:p[]",
        title="Fastest 2x2 Door",
        subtitle="All-time",
    )
    result = SimpleNamespace(id=3, status="unresolved", history_complete=True, gap_reasons={})
    session = AsyncMock(spec=AsyncSession)
    execute_result = MagicMock()
    execute_result.one_or_none.return_value = (result, definition)
    session.execute.return_value = execute_result
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    session.scalars.return_value = scalars_result
    loader = SearchProjectionLoader(cast(AsyncSession, session))

    projection = await loader.load("record", "result:3")

    assert projection is not None
    # With no holder, the definition's formatted title is the fallback, never the raw key.
    assert projection.title == "Fastest 2x2 Door"
    assert projection.subtitle == "All-time"
    assert projection.tags == ("fastest", "door", "all_time")
    assert projection.document_data["category_key"] == definition.category_key


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


def _session_holding_tag(**attributes: object) -> AsyncMock:
    definition = SimpleNamespace(
        **{
            "moderation_status": "approved",
            "authority": "official",
            "value_type": "none",
            "query_name": None,
            **attributes,
        }
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = definition
    # `scalars` is awaited and its result is not, so the result must be a plain object:
    # an AsyncMock child would hand back a coroutine from `.all()`.
    session.scalars.return_value = SimpleNamespace(all=lambda: ["seamless"])
    return session


@pytest.mark.parametrize("semantic_kind", ["restriction", "pattern", "showcase"])
@pytest.mark.asyncio
async def test_a_tag_document_says_which_kind_of_tag_it_is(semantic_kind: str) -> None:
    """Every approved tag used to index as `kind = tag`, whatever it actually was.

    That is why asking for patterns needed its own command: the one question the index
    could answer about a tag was that it was a tag.
    """
    session = _session_holding_tag(display_name="Full Lacing", semantic_kind=semantic_kind)
    loader = SearchProjectionLoader(cast(AsyncSession, session))

    projection = await loader.load("metadata", "tag:5")

    assert projection is not None
    assert projection.document_data["metadata_kind"] == semantic_kind
    assert (semantic_kind, "Full Lacing") in [(facet.field_name, facet.value) for facet in projection.facets]


@pytest.mark.asyncio
async def test_a_tag_document_still_answers_the_question_it_used_to() -> None:
    """`kind:tag` was the only taxonomy query there was, so it keeps working."""
    session = _session_holding_tag(display_name="Full Lacing", semantic_kind="pattern")
    loader = SearchProjectionLoader(cast(AsyncSession, session))

    projection = await loader.load("metadata", "tag:5")

    assert projection is not None
    kinds = {facet.value for facet in projection.facets if facet.field_name == "kind"}
    assert kinds == {"tag", "pattern"}


@pytest.mark.asyncio
async def test_an_unapproved_tag_is_not_indexed() -> None:
    session = _session_holding_tag(display_name="Full Lacing", semantic_kind="pattern", moderation_status="pending")
    loader = SearchProjectionLoader(cast(AsyncSession, session))

    assert await loader.load("metadata", "tag:5") is None
