"""Public schema acceptance tests for submission media routes."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

from squid.api.v1.schemas.submission_media import DraftMediaListResponse, DraftMediaResponse
from squid.media.application.jobs import MediaArtifactRole, MediaJobStatus, StoredMediaArtifact
from tests.unit.submissions.media_api_fakes import (
    DRAFT_ID,
    OTHER_UPLOAD_ID,
    UPLOAD_ID,
    FakeDrafts,
    FakeMedia,
    app_with_fakes,
    snapshot,
)

pytestmark = pytest.mark.asyncio


async def test_list_get_and_discard_keep_stable_states_without_private_artifacts() -> None:
    events: list[str] = []
    media = FakeMedia(events)
    output = StoredMediaArtifact(
        role=MediaArtifactRole.OUTPUT,
        object_key="private/normalized/secret",
        content_type="video/mp4",
        byte_size=10,
        sha256="b" * 64,
        width=8,
        height=6,
    )
    poster = StoredMediaArtifact(
        role=MediaArtifactRole.POSTER,
        object_key="private/posters/secret",
        content_type="image/jpeg",
        byte_size=3,
        sha256="c" * 64,
        width=8,
        height=6,
    )
    report = StoredMediaArtifact(
        role=MediaArtifactRole.REPORT,
        object_key="private/reports/secret",
        content_type="application/json",
        byte_size=20,
        sha256="d" * 64,
        width=None,
        height=None,
    )
    media.snapshots[UPLOAD_ID] = snapshot(
        status=MediaJobStatus.COMPLETED,
        artifacts=(report, poster, output),
    )
    media.snapshots[OTHER_UPLOAD_ID] = snapshot(upload_id=OTHER_UPLOAD_ID, status=MediaJobStatus.DEAD)
    app = app_with_fakes(media, FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media")
        get_response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media/{UPLOAD_ID}")
        delete_response = await client.delete(f"/submissions/drafts/{DRAFT_ID}/media/{OTHER_UPLOAD_ID}")

    assert list_response.status_code == 200
    assert list_response.headers["Cache-Control"] == "no-store"
    assert list_response.json()["limits"] == {
        "max_upload_bytes": 8,
        "max_images": 2,
        "max_videos": 1,
        "max_output_bytes": 16,
        "max_duration_milliseconds": 1_000,
        "max_pixels_per_frame": 100,
        "max_decoded_pixels_per_second": 200,
    }
    listed = {item["id"]: item for item in list_response.json()["media"]}
    assert listed[str(UPLOAD_ID)]["status"] == "completed"
    assert listed[str(OTHER_UPLOAD_ID)]["status"] == "dead"
    assert listed[str(UPLOAD_ID)]["artifacts"] == [
        {"role": "output", "content_type": "video/mp4", "width": 8, "height": 6},
        {"role": "video_thumbnail", "content_type": "image/jpeg", "width": 8, "height": 6},
    ]
    serialized = list_response.text
    assert "object_key" not in serialized
    assert "sha256" not in serialized
    assert "last_error" not in serialized
    assert "private decoder detail" not in serialized
    assert "report" not in serialized
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "completed"
    assert delete_response.status_code == 204
    assert delete_response.headers["Cache-Control"] == "no-store"
    assert media.discarded == (DRAFT_ID, OTHER_UPLOAD_ID)


async def test_media_dtos_reject_unknown_fields() -> None:
    completed = DraftMediaResponse.from_snapshot(snapshot())
    with pytest.raises(PydanticValidationError):
        DraftMediaResponse.model_validate({**completed.model_dump(), "source_object_key": "private/raw"})

    limits = {
        "max_upload_bytes": 8,
        "max_images": 2,
        "max_videos": 1,
        "max_output_bytes": 16,
        "max_duration_milliseconds": 1_000,
        "max_pixels_per_frame": 100,
        "max_decoded_pixels_per_second": 200,
    }
    with pytest.raises(PydanticValidationError):
        DraftMediaListResponse.model_validate({"limits": limits, "media": [], "report": {}})


async def test_openapi_advertises_streaming_binary_upload_without_a_json_wrapper() -> None:
    events: list[str] = []
    operation = app_with_fakes(FakeMedia(events), FakeDrafts(events)).openapi()["paths"][
        "/submissions/drafts/{draft_id}/media/{kind}"
    ]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    assert request_body["content"] == {
        "image/*": {"schema": {"type": "string", "format": "binary"}},
        "video/*": {"schema": {"type": "string", "format": "binary"}},
    }
