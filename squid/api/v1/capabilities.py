"""Public API capability discovery."""

from fastapi import APIRouter

from squid.api.capabilities import API_FEATURES, API_VERSION, RENDERER_CAPABILITIES, RENDERER_CONTROLS
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.v1.schemas.capabilities import (
    ApiCapabilities,
    ApiFeatureCapabilities,
    ApiVersionCapabilities,
    ProtocolCapabilities,
    ProtocolInterval,
    RendererCapabilities,
    SanitizationCapabilities,
    UploadCapabilities,
)
from squid.media.domain import MediaLimits
from squid.submissions.application import CURRENT_SUBMISSION_PROTOCOL

router = APIRouter(tags=["capabilities"])


@router.get(
    "/capabilities",
    response_model=ApiCapabilities,
    operation_id="capabilities_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def capabilities() -> ApiCapabilities:
    """Publish independently versioned client compatibility and safety limits."""
    limits = MediaLimits()
    protocol = ProtocolInterval(
        minimum=CURRENT_SUBMISSION_PROTOCOL,
        maximum=CURRENT_SUBMISSION_PROTOCOL,
    )
    return ApiCapabilities(
        api=ApiVersionCapabilities(semantic_version=API_VERSION),
        features=ApiFeatureCapabilities(identifiers=tuple(sorted(API_FEATURES))),
        protocols=ProtocolCapabilities(submission=protocol),
        uploads=UploadCapabilities(
            max_images=limits.max_images,
            max_videos=limits.max_videos,
            max_duration_milliseconds=limits.max_duration_milliseconds,
            max_source_bytes=limits.max_source_bytes,
            max_output_bytes=limits.max_output_bytes,
            max_pixels_per_frame=limits.max_pixels_per_frame,
            max_decoded_pixels_per_second=limits.max_decoded_pixels_per_second,
        ),
        renderer=RendererCapabilities(
            controls=RENDERER_CONTROLS,
            capability_identifiers=RENDERER_CAPABILITIES,
        ),
        sanitization=SanitizationCapabilities(),
    )
