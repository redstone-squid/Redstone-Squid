"""Public catalogue API contract extensions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from squid.api.v1.records import get_record
from squid.api.v1.schemas.builds import BuildDetail, BuildSummary
from squid.builds.domain import Build, BuildCategory, Status
from squid.core.errors import DataIntegrityError
from squid.records.application.models import ActiveRecord
from squid.tags.domain import (
    TagAssignment,
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)


def catalogue_build(build_id: int, **changes: Any) -> Build:
    values: dict[str, Any] = {
        "id": build_id,
        "submitter_id": 123,
        "category": BuildCategory.DOOR,
        "submission_status": Status.CONFIRMED,
        "versions": ["Java 1.21.5"],
        "version_spec": ">=1.21",
        "width": 3,
        "height": 4,
        "depth": 5,
        "door_width": 2,
        "door_height": 3,
        "door_type": ["Regular"],
        "door_orientation_type": "Door",
        "normal_opening_time": 8,
        "normal_closing_time": 10,
    }
    values.update(changes)
    return Build(**values)


def active_record(*holder_build_ids: int) -> ActiveRecord:
    return ActiveRecord(
        id=7,
        definition_id=3,
        competition_id=UUID("22222222-2222-2222-2222-222222222222"),
        title="Fastest 2x3 door",
        subtitle="All versions",
        record_class="fastest",
        build_kind="door",
        version_scope="all-time",
        status="resolved",
        holder_build_ids=holder_build_ids,
        computed_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )


def test_build_summary_exposes_catalogue_card_fields_and_stable_tag_keys() -> None:
    definition = TagDefinition(
        id=4,
        stable_key="official_seamless",
        display_name="Seamless",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=TagSemanticKind.RESTRICTION,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
    )
    build = catalogue_build(
        42,
        tags=[TagAssignment(definition)],
        render_urls=["http://unsafe.example/render.png", "https://media.example/render.png"],
        image_urls=["https://media.example/submitted.png"],
    )

    summary = BuildSummary.from_domain(build)

    assert summary.preview is not None
    assert summary.preview.model_dump() == {"kind": "render", "url": "https://media.example/render.png"}
    assert summary.version_spec == ">=1.21"
    assert summary.versions == ["Java 1.21.5"]
    assert summary.opening_time == 8
    assert summary.closing_time == 10
    assert summary.tags[0].key == "official_seamless"


@pytest.mark.parametrize(
    ("renders", "images", "expected"),
    [
        (["not a URL"], ["https://media.example/image.png"], ("image", "https://media.example/image.png")),
        (["http://media.example/render.png"], ["http://media.example/image.png"], None),
        ([], [], None),
    ],
)
def test_build_summary_uses_only_valid_https_previews(
    renders: list[str],
    images: list[str],
    expected: tuple[str, str] | None,
) -> None:
    preview = BuildSummary.from_domain(catalogue_build(42, render_urls=renders, image_urls=images)).preview

    assert ((preview.kind, preview.url) if preview is not None else None) == expected


def test_build_detail_inherits_card_fields_without_duplicate_constructor_values() -> None:
    detail = BuildDetail.from_domain(
        catalogue_build(
            42,
            render_urls=["https://media.example/render.png"],
            description="A compact door.",
        )
    )

    assert detail.preview is not None
    assert detail.versions == ["Java 1.21.5"]
    assert detail.opening_time == 8
    assert detail.description == "A compact door."


@pytest.mark.asyncio
async def test_record_detail_hydrates_holders_in_record_order() -> None:
    record = active_record(41, 42)
    records = SimpleNamespace(get=AsyncMock(return_value=record))
    build_queries = SimpleNamespace(get_many=AsyncMock(return_value=[catalogue_build(42), catalogue_build(41)]))

    detail = await get_record(7, cast(Any, records), cast(Any, build_queries))

    assert detail.holder_build_ids == [41, 42]
    assert [build.id for build in detail.holder_builds] == [41, 42]
    build_queries.get_many.assert_awaited_once_with((41, 42))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "available",
    [
        [catalogue_build(41)],
        [catalogue_build(41), catalogue_build(42, submission_status=Status.DENIED)],
    ],
)
async def test_record_detail_rejects_unavailable_or_non_public_holders(available: list[Build]) -> None:
    records = SimpleNamespace(get=AsyncMock(return_value=active_record(41, 42)))
    build_queries = SimpleNamespace(get_many=AsyncMock(return_value=available))

    with pytest.raises(DataIntegrityError) as exc_info:
        await get_record(7, cast(Any, records), cast(Any, build_queries))

    assert exc_info.value.context == {"record_id": 7, "unavailable_holder_build_ids": [42]}
