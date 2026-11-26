"""Tests for the field values that builds, records and tags project."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.engine import IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild, OtherBuild
from squid.core.search.infrastructure.documents import FieldValue, SearchProjection
from squid.core.search.infrastructure.projection import (
    RecordHolderProjectionSource,
    RecordProjectionSource,
    SearchProjectionLoader,
    TagProjectionSource,
    build_record_projection,
    build_tag_projection,
    parse_projection_key,
)
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAlias, TagAssignment, TagDefinition

_RECORD_ID = UUID("6f1c2a4e-0d3b-4c5a-9e7f-1a2b3c4d5e6f")


def test_records_are_keyed_by_their_public_id() -> None:
    assert parse_projection_key("record", f"record:{_RECORD_ID}") == ("record", _RECORD_ID)
    assert parse_projection_key("record", "result:1") is None
    assert parse_projection_key("record", "record:not-a-uuid") is None


def _record_source(*, result_id: int | None = 3, **changes: object) -> RecordProjectionSource:
    source = RecordProjectionSource(
        record_id=_RECORD_ID,
        result_id=result_id,
        definition_id=7,
        history_complete=True,
        gap_reasons={},
        criterion="fastest",
        build_kind="door",
        version_scope="all_time",
        category_key="door:door|2x2|t[20]|Door:r[]:p[]",
        title="Fastest 2x2 Door",
        subtitle="All-time",
    )
    return replace(source, **changes)  # type: ignore[arg-type]


def test_record_documents_keep_category_text_apart_from_holder_text() -> None:
    source = _record_source()

    projection = build_record_projection(source)

    assert projection.source_key == f"record:{_RECORD_ID}"
    assert projection.title == "Fastest 2x2 Door"
    assert projection.status == "current"
    assert FieldValue("title", "Fastest 2x2 Door") in projection.values
    assert FieldValue("subtitle", "All-time") in projection.values
    assert FieldValue("base_title", "fastest") in projection.values
    assert projection.document_data["category_key"] == source.category_key

    held = build_record_projection(
        source,
        (
            RecordHolderProjectionSource(42, "2x2 Seamless Door", {"opening": 4}, ("Alice",)),
            RecordHolderProjectionSource(57, "2x2 Compact Door", {"opening": 4}, ("Bob",), ("bob-id",)),
        ),
    )
    assert held.description is None
    assert FieldValue("holder_title", "2x2 Seamless Door", 42) in held.values
    assert FieldValue("holder_creator", "Bob", 57) in held.values
    assert FieldValue("creator", "Bob") in held.values
    assert FieldValue("creator_id", "bob-id") in held.values
    assert held.document_data["holder_build_ids"] == (42, 57)
    assert held.document_data["build_title"] == "2x2 Seamless Door"
    assert held.document_data["holder_metrics"] == {"42": {"opening": 4}, "57": {"opening": 4}}


def test_a_retired_record_keeps_its_key_and_lists_former_holders() -> None:
    projection = build_record_projection(_record_source(result_id=None, former_holder_ids=(9, 12), tied_build_ids=()))

    assert projection.source_key == f"record:{_RECORD_ID}"
    assert projection.status == "retired"
    assert FieldValue("record_state", "retired") in projection.values
    assert FieldValue("former_holder", Decimal(9)) in projection.values
    assert FieldValue("former_holder", Decimal(12)) in projection.values


def test_ties_are_indexed_apart_from_holders() -> None:
    projection = build_record_projection(
        _record_source(tied_build_ids=(77,)), (RecordHolderProjectionSource(42, "Door", {}),)
    )

    assert FieldValue("tied", Decimal(77)) in projection.values
    assert not any(value.field == "holder" and value.value == "77" for value in projection.values)


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
    assert FieldValue(semantic_kind, "Full Lacing") in projection.values


def test_a_tag_document_still_answers_the_question_it_used_to() -> None:
    """Retain the broad kind:tag filter alongside the specific kind."""
    projection = build_tag_projection(_tag_source(semantic_kind="pattern"))

    assert projection is not None
    kinds = {value.value for value in projection.values if value.field == "kind"}
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
        assert projection.document_data["tags"] == ("No Observers",)
        names = {(value.field, value.value) for value in projection.values}
        assert ("tag", "Observerless") in names
        assert ("tag", "No Observers") in names
        if semantic_kind is TagSemanticKind.RESTRICTION:
            assert ("restriction", "Observerless") in names
        elif semantic_kind is TagSemanticKind.PATTERN:
            assert ("pattern", "Observerless") in names
            assert ("type", "Observerless") in names
    else:
        assert projection.document_data["tags"] == ()
        assert not any(value.value in {"No Observers", "Observerless"} for value in projection.values)


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
    filters = {value.field: value.value for value in projection.values}
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


async def test_build_projection_indexes_its_name_orientation_and_extender_fields() -> None:
    now = Instant.parse_iso("2026-11-16T12:00:00Z")
    build = ExtenderBuild(orientation="Horizontal", extension_length=4, extender_type="Double", extension_time=6)
    build.id = 42
    build.display_name = "hidden piston extender"
    build.submission_status = BuildStatus.APPROVED
    build.submission_time = build.edited_time = now
    projection = await _project_build(build)

    assert projection is not None
    assert FieldValue("name", "hidden piston extender") in projection.values
    assert FieldValue("orientation", "Horizontal") in projection.values
    assert FieldValue("extender_length", Decimal(4)) in projection.values
    assert FieldValue("type", "Double") in projection.values
    assert FieldValue("extension_time", Decimal(6)) in projection.values
