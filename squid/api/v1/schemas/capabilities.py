"""Typed API compatibility and resource-limit capability response."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.api.capabilities import RendererControl


class ProtocolInterval(BaseModel):
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


class ApiVersionCapabilities(BaseModel):
    """Version of the public HTTP API, independent of payload protocols."""

    model_config = ConfigDict(frozen=True)

    semantic_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ApiFeatureCapabilities(BaseModel):
    """Stable feature identifiers understood by this API deployment."""

    model_config = ConfigDict(frozen=True)

    identifiers: tuple[str, ...]


class ProtocolCapabilities(BaseModel):
    """Compatibility intervals for protocols outside HTTP API SemVer."""

    model_config = ConfigDict(frozen=True)

    submission: ProtocolInterval


class UploadCapabilities(BaseModel):
    """Upload, aggregate, and decoder-work limits enforced by the backend."""

    model_config = ConfigDict(frozen=True)

    max_images: int = Field(gt=0)
    max_videos: int = Field(gt=0)
    max_duration_milliseconds: int = Field(gt=0)
    max_source_bytes: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_pixels_per_frame: int = Field(gt=0)
    max_decoded_pixels_per_second: int = Field(gt=0)


class RendererCapabilities(BaseModel):
    """Form controls and optional renderer features emitted by the API."""

    model_config = ConfigDict(frozen=True)

    controls: tuple[RendererControl, ...]
    capability_identifiers: tuple[str, ...]


class SanitizationCapabilities(BaseModel):
    """Artifact transformations whose availability is compatibility-relevant."""

    model_config = ConfigDict(frozen=True)

    media: Literal["normalization"] = "normalization"
    schematics: Literal["unavailable"] = "unavailable"


class ApiCapabilities(BaseModel):
    """Namespaced compatibility facts for generated and handwritten clients."""

    model_config = ConfigDict(frozen=True)

    api: ApiVersionCapabilities
    features: ApiFeatureCapabilities
    protocols: ProtocolCapabilities
    uploads: UploadCapabilities
    renderer: RendererCapabilities
    sanitization: SanitizationCapabilities
