"""Tests for search projection normalization and typed filters."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine import IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, DoorBuild, OtherBuild
from squid.core.search.infrastructure.projection import (
    ProjectionFilter,
    RecordHolderProjectionSource,
    RecordProjectionSource,
    SearchProjection,
    SearchProjectionLoader,
    TagProjectionSource,
    build_filter_models,
    build_record_projection,
    build_tag_projection,
    normalize_search_text,
    parse_projection_key,
    projection_source_hash,
)
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAlias, TagAssignment, TagDefinition


def test_loader_rejects_retired_legacy_record_keys() -> None:
    assert parse_projection_key("record", "legacy-smallest:1") is None


def test_record_documents_carry_titles_not_category_keys() -> None:
    source = RecordProjectionSource(
        result_id=3,
        definition_id=7,
        status="unresolved",
        history_complete=True,
        gap_reasons={},
        criterion="fastest",
        build_kind="door",
        version_scope="all_time",
        category_key="door:door|2x2|t[20]|Door:r[]:p[]",
        title="Fastest 2x2 Door",
        subtitle="All-time",
    )

    projection = build_record_projection(source)

    # With no holder, the definition's formatted title is the fallback, never the raw key.
    assert projection.title == "Fastest 2x2 Door"
    assert projection.subtitle == "All-time"
    assert projection.tags == ("fastest", "door", "all_time")
    assert projection.document_data["category_key"] == source.category_key

    held = build_record_projection(
        source,
        (
            RecordHolderProjectionSource(42, "2x2 Seamless Door", {"opening": 4}, ("Alice",)),
            RecordHolderProjectionSource(57, "2x2 Compact Door", {"opening": 4}, ("Bob",), ("bob-id",)),
        ),
    )
    assert held.description == "2x2 Seamless Door Alice\n2x2 Compact Door Bob"
    assert ProjectionFilter("creator", "Bob") in held.filters
    assert ProjectionFilter("creator_id", "bob-id") in held.filters
    assert held.title == source.title
    assert held.subtitle == source.subtitle
    assert held.document_data["holder_build_ids"] == (42, 57)
    assert held.document_data["build_title"] == "2x2 Seamless Door"
    assert held.document_data["holder_metrics"] == {"42": {"opening": 4}, "57": {"opening": 4}}


def test_normalize_collapses_case_and_whitespace() -> None:
    assert normalize_search_text("  Three   BY   Three ") == "three by three"


def test_source_hash_changes_with_searchable_content() -> None:
    original = SearchProjection(resource_kind="build", source_key="1", title="3x3 Door")
    edited = SearchProjection(resource_kind="build", source_key="1", title="Fast 3x3 Door")

    assert projection_source_hash(original) == projection_source_hash(original)
    assert projection_source_hash(original) != projection_source_hash(edited)
    assert projection_source_hash(original) != projection_source_hash(replace(original, search_aliases=("Compact",)))


def test_filter_models_assign_per_field_ordinals_and_typed_columns() -> None:
    completion = Instant.parse_iso("2026-07-30T12:00:00Z")
    models = build_filter_models(
        42,
        (
            ProjectionFilter("tag", "slimeless"),
            ProjectionFilter("tag", "seamless"),
            ProjectionFilter("volume", Decimal(27)),
            ProjectionFilter("completion_at", completion),
            ProjectionFilter("verified", value=True),
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


def _tag_source(*, semantic_kind: str = "pattern", moderation_status: str = "approved") -> TagProjectionSource:
    return TagProjectionSource(
        tag_id=5,
        display_name="Full Lacing",
        semantic_kind=semantic_kind,
        moderation_status=moderation_status,
        authority="official",
        value_type="none",
        query_name=None,
        aliases=("seamless",),
    )


@pytest.mark.parametrize("semantic_kind", ["restriction", "pattern", "showcase"])
def test_a_tag_document_says_which_kind_of_tag_it_is(semantic_kind: str) -> None:
    """Index the specific tag kind so tag queries can distinguish patterns and restrictions."""
    projection = build_tag_projection(_tag_source(semantic_kind=semantic_kind))

    assert projection is not None
    assert projection.document_data["metadata_kind"] == semantic_kind
    assert (semantic_kind, "Full Lacing") in [
        (filter_value.field_name, filter_value.value) for filter_value in projection.filters
    ]


def test_a_tag_document_still_answers_the_question_it_used_to() -> None:
    """Retain the broad kind:tag filter alongside the specific kind."""
    projection = build_tag_projection(_tag_source(semantic_kind="pattern"))

    assert projection is not None
    kinds = {filter_value.value for filter_value in projection.filters if filter_value.field_name == "kind"}
    assert kinds == {"tag", "pattern"}


def test_an_unapproved_tag_is_not_indexed() -> None:
    assert build_tag_projection(_tag_source(semantic_kind="pattern", moderation_status="pending")) is None


async def _project_build(build: Build) -> SearchProjection | None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = build
    metadata = SimpleResultMetaData(("value",))
    session.scalars.side_effect = lambda _: IteratorResult(metadata, iter(())).scalars()
    session.execute.side_effect = lambda _: IteratorResult(metadata, iter(()))
    return await SearchProjectionLoader(session).load("build", str(build.id))


@pytest.mark.parametrize("semantic_kind", list(TagSemanticKind))
@pytest.mark.parametrize("status", [TagModerationStatus.APPROVED, TagModerationStatus.PENDING])
async def test_build_aliases_are_searchable_only_for_approved_assignments(
    semantic_kind: TagSemanticKind, status: TagModerationStatus
) -> None:
    definition = TagDefinition(
        stable_key="no-observers",
        display_name="No Observers",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=semantic_kind,
        value_type=TagValueType.NONE,
        moderation_status=status,
        aliases=[TagAlias(alias="Observerless", normalized_alias="observerless")],
    )
    build = OtherBuild(tags=[TagAssignment(definition)])
    build.id = 42
    build.submission_status = BuildStatus.APPROVED
    build.submission_time = build.edited_time = Instant.now()
    projection = await _project_build(build)

    assert projection is not None
    if status is TagModerationStatus.APPROVED:
        assert projection.search_aliases == ("Observerless",)
        assert projection.tags == ("No Observers",)
        names = {(filter_value.field_name, filter_value.value) for filter_value in projection.filters}
        assert ("tag", "Observerless") in names
        assert ("tag", "No Observers") in names
        if semantic_kind is TagSemanticKind.RESTRICTION:
            assert ("restriction", "Observerless") in names
        elif semantic_kind is TagSemanticKind.PATTERN:
            assert ("pattern", "Observerless") in names
            assert ("type", "Observerless") in names
    else:
        assert projection.search_aliases == ()
        assert projection.tags == ()
        assert not any(filter_value.value in {"No Observers", "Observerless"} for filter_value in projection.filters)


@pytest.mark.parametrize("door", [True, False])
@pytest.mark.parametrize("depth", [5, None])
async def test_build_projection_keeps_opening_dimensions_separate(door: bool, depth: int | None) -> None:
    now = Instant.parse_iso("2026-11-16T12:00:00Z")
    build = DoorBuild(door_width=3, door_height=4) if door else OtherBuild()
    build.id = 42
    build.width, build.height, build.depth = 10, 12, depth
    build.submission_status = BuildStatus.APPROVED
    build.submission_time = build.edited_time = now
    projection = await _project_build(build)

    assert projection is not None
    filters = {filter_value.field_name: filter_value.value for filter_value in projection.filters}
    assert filters["width"] == Decimal(10)
    assert filters["height"] == Decimal(12)
    if depth is None:
        assert "depth" not in filters
        assert "volume" not in filters
    else:
        assert filters["depth"] == Decimal(5)
        assert filters["volume"] == Decimal(600)
    assert "door_depth" not in filters
    if door:
        assert filters["door_width"] == Decimal(3)
        assert filters["door_height"] == Decimal(4)
    else:
        assert "door_width" not in filters
        assert "door_height" not in filters
