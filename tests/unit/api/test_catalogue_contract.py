"""Public catalogue API contract extensions."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from squid.api.v1.records import get_record
from squid.api.v1.schemas.builds import BuildDetail, BuildSummary, DoorDetails, ExtenderDetails, GeneralDetails
from squid.builds.application import BuildQueryService
from squid.builds.domain import Build, DoorBuild, ExtenderBuild, MediaTypeLiteral, Status, UtilityBuild
from squid.core.errors import DataIntegrityError
from squid.records.application.models import PublishedRecord
from squid.records.application.services import RecordService
from squid.sponsors import PublicSponsor
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
        "submitter_account_id": 123,
        "submission_status": Status.CONFIRMED,
        "versions": ["Java 1.21.5"],
        "version_spec": ">=1.21",
        "width": 3,
        "height": 4,
        "depth": 5,
        "door_width": 2,
        "door_height": 3,
        "patterns": ["Regular"],
        "orientation": "Door",
        "normal_opening_time": 8,
        "normal_closing_time": 10,
    }
    url_fields = {name: changes.pop(name) for name in ("image_urls", "video_urls", "render_urls") if name in changes}
    values.update(changes)
    build = DoorBuild(**values)
    media_types: dict[str, MediaTypeLiteral] = {
        "image_urls": "image",
        "video_urls": "video",
        "render_urls": "render",
    }
    for name, urls in url_fields.items():
        build.replace_links(media_types[name], urls)
    return build


def published_record(*holder_build_ids: int) -> PublishedRecord:
    return PublishedRecord(
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


class RecordFake(RecordService):
    def __init__(self, record: PublishedRecord) -> None:
        self.record = record

    async def get(self, result_id: int) -> PublishedRecord | None:
        return self.record if result_id == self.record.id else None


class BuildQueryFake(BuildQueryService):
    def __init__(self, builds: list[Build]) -> None:
        self.builds = builds
        self.requests: list[tuple[int, ...]] = []

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        self.requests.append(tuple(build_ids))
        return self.builds


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
        display_name="Vault prototype",
        tags=[TagAssignment(definition)],
        render_urls=["http://unsafe.example/render.png", "https://media.example/render.png"],
        image_urls=["https://media.example/submitted.png"],
    )

    summary = BuildSummary.from_domain(build)

    assert summary.preview is not None
    assert summary.display_name == "Vault prototype"
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


def test_build_detail_exposes_only_the_immutable_public_sponsor_projection() -> None:
    installation_id = UUID("33333333-3333-4333-8333-333333333333")
    detail = BuildDetail.from_domain(
        catalogue_build(
            42,
            sponsor=PublicSponsor(
                installation_id,
                display_name="Example server",
                address="play.example.test",
                description="A public profile",
                website_url="https://example.test/server",
            ),
        )
    )

    assert detail.sponsor is not None
    assert detail.sponsor.model_dump(mode="json") == {
        "installation_id": str(installation_id),
        "display_name": "Example server",
        "address": "play.example.test",
        "description": "A public profile",
        "website_url": "https://example.test/server",
    }


@pytest.mark.asyncio
async def test_record_detail_hydrates_holders_in_record_order() -> None:
    record = published_record(41, 42)
    records = RecordFake(record)
    build_queries = BuildQueryFake([catalogue_build(42), catalogue_build(41)])

    detail = await get_record(7, records, build_queries)

    assert detail.holder_build_ids == [41, 42]
    assert [build.id for build in detail.holder_builds] == [41, 42]
    assert build_queries.requests == [(41, 42)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "available",
    [
        [catalogue_build(41)],
        [catalogue_build(41), catalogue_build(42, submission_status=Status.DENIED)],
    ],
)
async def test_record_detail_rejects_unavailable_or_non_public_holders(available: list[Build]) -> None:
    records = RecordFake(published_record(41, 42))
    build_queries = BuildQueryFake(available)

    with pytest.raises(DataIntegrityError) as exc_info:
        await get_record(7, records, build_queries)

    assert exc_info.value.context == {"record_id": 7, "unavailable_holder_build_ids": [42]}


def test_detail_details_are_keyed_by_the_build_category() -> None:
    """Category-specific facts arrive under a member matching the category."""
    door = BuildDetail.from_domain(catalogue_build(1))
    assert isinstance(door.details, DoorDetails)
    assert door.details.category == "Door"
    assert door.details.door_dimensions.width == 2
    assert door.details.orientation == "Door"
    assert door.details.patterns == ["Regular"]

    extender = BuildDetail.from_domain(
        ExtenderBuild(
            id=2,
            submitter_account_id=1,
            submission_status=Status.CONFIRMED,
            orientation="Upward",
            extension_length=6,
        )
    )
    assert isinstance(extender.details, ExtenderDetails)
    assert extender.details.category == "Extender"
    assert extender.details.extension_length == 6

    utility = BuildDetail.from_domain(UtilityBuild(id=3, submitter_account_id=1, submission_status=Status.CONFIRMED))
    assert isinstance(utility.details, GeneralDetails)
    assert utility.details.category == "Utility"
