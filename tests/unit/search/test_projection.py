"""Tests for search projection normalization and typed facets."""

from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.search.infrastructure.models import SearchProjectionQueueItem
from squid.search.infrastructure.projection import (
    ProjectionFacet,
    SearchProjection,
    SearchProjectionLoader,
    SearchProjectionStore,
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
async def test_projection_failure_becomes_a_retained_dead_letter_at_attempt_limit() -> None:
    session = AsyncMock(spec=AsyncSession)
    store = SearchProjectionStore(cast(AsyncSession, session))
    item = SearchProjectionQueueItem(resource_kind="build", source_key="42", action="upsert", attempts=4)
    item.locked_at = Instant.now()

    dead_lettered = await store.retry(item, RuntimeError("projection failed"), max_attempts=5)

    assert dead_lettered is True
    assert item.attempts == 5
    assert item.locked_at is None
    assert item.dead_at is not None
    assert item.last_error == "projection failed"
    session.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_projection_failure_is_released_before_attempt_limit() -> None:
    session = AsyncMock(spec=AsyncSession)
    store = SearchProjectionStore(cast(AsyncSession, session))
    item = SearchProjectionQueueItem(resource_kind="build", source_key="42", action="upsert")
    item.locked_at = Instant.now()

    dead_lettered = await store.retry(item, RuntimeError("temporary"), max_attempts=5)

    assert dead_lettered is False
    assert item.attempts == 1
    assert item.locked_at is None
    assert item.dead_at is None


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
