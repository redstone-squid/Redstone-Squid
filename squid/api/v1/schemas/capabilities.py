"""Typed API compatibility and resource-limit capability response."""

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from squid.api.capabilities import RendererControl
from squid.api.schema import ApiSchema


class ProtocolInterval(ApiSchema):
    """Inclusive versions accepted for one independently versioned protocol."""

    model_config = ConfigDict(frozen=True)

    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.maximum < self.minimum:
            msg = "Protocol maximum cannot be lower than its minimum."
            raise ValueError(msg)
        return self

    def supports(self, version: int) -> bool:
        """Return whether a client protocol version overlaps this interval."""
        return self.minimum <= version <= self.maximum


class ApiVersionCapabilities(ApiSchema):
    """Version of the public HTTP API, independent of payload protocols."""

    model_config = ConfigDict(frozen=True)

    semantic_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ApiFeatureCapabilities(ApiSchema):
    """Stable feature identifiers understood by this API deployment.

    They name what this server can do, not what any client supports. An operation's
    `x-squid-cli.required_api_features` names the identifiers it needs.
    """

    model_config = ConfigDict(frozen=True)

    identifiers: tuple[str, ...] = Field(description="Sorted ascending, so two deployments compare directly.")


class ProtocolCapabilities(ApiSchema):
    """Compatibility intervals for protocols outside HTTP API SemVer."""

    model_config = ConfigDict(frozen=True)

    submission: ProtocolInterval = Field(
        description="Submission form protocol versions this server serves; compare against a form manifest's "
        "`minimum_protocol` and `maximum_protocol`."
    )


class UploadCapabilities(ApiSchema):
    """Upload, aggregate, and decoder-work limits enforced by the backend.

    Exceeding any of them is rejected server-side; a client that checks first spares the upload.
    """

    model_config = ConfigDict(frozen=True)

    max_images: int = Field(gt=0, description="Images per draft.")
    max_videos: int = Field(gt=0, description="Videos per draft.")
    max_duration_milliseconds: int = Field(gt=0, description="Longest accepted video, per file.")
    max_source_bytes: int = Field(gt=0, description="Total uploaded bytes across a draft, not one file.")
    max_output_bytes: int = Field(gt=0, description="Total normalized bytes across a draft, not one file.")
    max_pixels_per_frame: int = Field(gt=0, description="Width times height of one decoded frame, per file.")
    max_decoded_pixels_per_second: int = Field(
        gt=0, description="Pixels per frame times frame rate, per video file. Images are exempt."
    )


class RendererCapabilities(ApiSchema):
    """Form controls and optional renderer features emitted by the API."""

    model_config = ConfigDict(frozen=True)

    controls: tuple[RendererControl, ...] = Field(
        description="Every `control` a form field may carry, so a client can refuse a manifest it cannot draw."
    )
    capability_identifiers: tuple[str, ...] = Field(
        description="Optional renderer behaviours a form field may require through `required_capability`."
    )


class SanitizationCapabilities(ApiSchema):
    """Artifact transformations whose availability is compatibility-relevant.

    Both values are fixed for this deployment: uploaded media is re-encoded before publication, and
    no schematic sanitizer runs.
    """

    model_config = ConfigDict(frozen=True)

    media: Literal["normalization"] = "normalization"
    schematics: Literal["unavailable"] = "unavailable"


class ApiCapabilities(ApiSchema):
    """Namespaced compatibility facts for generated and handwritten clients."""

    model_config = ConfigDict(frozen=True)

    api: ApiVersionCapabilities
    features: ApiFeatureCapabilities
    protocols: ProtocolCapabilities
    uploads: UploadCapabilities
    renderer: RendererCapabilities
    sanitization: SanitizationCapabilities
