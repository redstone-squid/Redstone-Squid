"""Media metadata, normalization reports, and submission resource limits."""

from dataclasses import dataclass
from enum import StrEnum

from squid.core.errors import ValidationError
from squid.core.i18n import tr

MEBIBYTE = 1024 * 1024

VIDEO_CONTENT_TYPES = frozenset(
    {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "video/x-msvideo", "video/mpeg"}
)


ORIGINAL_IMAGE_FORMATS = {
    "png": ("png_pipe", "image/png"),
    "apng": ("apng", "image/apng"),
    "mjpeg": ("jpeg_pipe", "image/jpeg"),
    "gif": ("gif", "image/gif"),
    "webp": ("webp_pipe", "image/webp"),
}
IMAGE_CONTENT_TYPES = frozenset(content_type for _, content_type in ORIGINAL_IMAGE_FORMATS.values())


class MediaKind(StrEnum):
    """A hosted media type accepted by a submission."""

    IMAGE = "image"
    VIDEO = "video"


class MediaLimitMeasure(StrEnum):
    """Stable identifiers for submission capacity limits."""

    IMAGE_COUNT = "image_count"
    VIDEO_COUNT = "video_count"
    SOURCE_BYTES = "source_bytes"
    OUTPUT_BYTES = "output_bytes"


@dataclass(frozen=True, slots=True)
class MediaViolation:
    """A measured resource value and the limit it exceeded."""

    measure: MediaLimitMeasure
    actual: int
    limit: int


@dataclass(frozen=True, slots=True)
class MediaBatchTotals:
    """Aggregate media resources reserved by one submission."""

    image_count: int = 0
    video_count: int = 0
    source_bytes: int = 0
    output_bytes: int = 0

    def __post_init__(self) -> None:
        if min(self.image_count, self.video_count, self.source_bytes, self.output_bytes) < 0:
            msg = tr(t"media-media-batch-totals = Media batch totals cannot be negative.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Image/video counts and byte budgets for one submission."""

    max_images: int = 10
    max_videos: int = 3

    max_source_bytes: int = 500 * MEBIBYTE
    max_output_bytes: int = 500 * MEBIBYTE

    def __post_init__(self) -> None:
        if (
            min(
                self.max_images,
                self.max_videos,
                self.max_source_bytes,
                self.max_output_bytes,
            )
            <= 0
        ):
            msg = tr(t"media-every-media-limit = Every media limit must be positive.")
            raise ValidationError(msg)

    def batch_violations(self, totals: MediaBatchTotals) -> tuple[MediaViolation, ...]:
        """Return every aggregate violation in a stable public order."""
        checks = (
            (MediaLimitMeasure.IMAGE_COUNT, totals.image_count, self.max_images),
            (MediaLimitMeasure.VIDEO_COUNT, totals.video_count, self.max_videos),
            (MediaLimitMeasure.SOURCE_BYTES, totals.source_bytes, self.max_source_bytes),
            (MediaLimitMeasure.OUTPUT_BYTES, totals.output_bytes, self.max_output_bytes),
        )
        return tuple(MediaViolation(measure, actual, limit) for measure, actual, limit in checks if actual > limit)


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """Stream metadata reported by the media inspector.

    Frame rate is a rational number of frames per second. A zero numerator means
    no usable rate was reported. Duration and frame count are None when unknown;
    audio_codec is None when no audio stream was found. Width and height are
    display dimensions after container rotation.
    """

    container_names: tuple[str, ...]
    video_codec: str
    width: int
    height: int
    frame_rate_numerator: int
    frame_rate_denominator: int
    duration_milliseconds: int | None
    audio_codec: str | None = None
    frame_count: int | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            msg = tr(t"media-media-dimensions-positive = Media dimensions must be positive.")
            raise ValidationError(msg)
        if self.frame_rate_numerator < 0 or self.frame_rate_denominator <= 0:
            msg = tr(t"media-media-frame-rate = Media frame rate must be non-negative with a positive denominator.")
            raise ValidationError(msg)
        if self.duration_milliseconds is not None and self.duration_milliseconds < 0:
            msg = tr(t"media-media-duration-negative = Media duration cannot be negative.")
            raise ValidationError(msg)
        if self.frame_count is not None and self.frame_count < 0:
            msg = tr(t"media-media-frame-count = Media frame count cannot be negative.")
            raise ValidationError(msg)

    @property
    def original_image_content_type(self) -> str | None:
        """Recognize raster image containers independently of supplied filenames and MIME types."""
        match = ORIGINAL_IMAGE_FORMATS.get(self.video_codec)
        if match is not None and match[0] in self.container_names:
            return match[1]
        return None

    @property
    def has_audio(self) -> bool:
        """Whether the inspected input contains an audio stream."""
        return self.audio_codec is not None


class MediaNormalizationAction(StrEnum):
    """Stable, non-sensitive actions included in normalization reports."""

    # Historical actions remain readable in immutable reports.
    METADATA_REMOVED = "metadata_removed"
    IMAGE_REENCODED = "image_reencoded"
    VIDEO_TRANSCODED = "video_transcoded"
    AUDIO_PRESERVED = "audio_preserved"
    AUDIO_REMOVED = "audio_removed"
    POSTER_GENERATED = "poster_generated"


@dataclass(frozen=True, slots=True)
class MediaArtifact:
    """Content facts for one completed normalized artifact."""

    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.byte_size <= 0 or self.width <= 0 or self.height <= 0:
            msg = tr(t"media-normalized-artifact-dimensions = Normalized artifact dimensions and size are invalid.")
            raise ValidationError(msg)
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            msg = tr(t"media-normalized-artifact-sha = Normalized artifact SHA-256 must be lowercase hexadecimal.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaNormalizationReport:
    """Input and output metadata, artifact checksums, and actions taken during normalization."""

    kind: MediaKind
    source_bytes: int
    input_probe: MediaProbe
    output_probe: MediaProbe
    output: MediaArtifact
    poster: MediaArtifact | None
    actions: tuple[MediaNormalizationAction, ...]

    def __post_init__(self) -> None:
        if self.source_bytes < 0:
            msg = tr(t"media-normalized-media-source = Normalized media source size cannot be negative.")
            raise ValidationError(msg)
        if self.kind is MediaKind.IMAGE and self.poster is not None:
            msg = tr(t"media-normalized-videos-poster = Only normalized videos have a poster artifact.")
            raise ValidationError(msg)
        if self.kind is MediaKind.VIDEO and self.poster is None:
            msg = tr(t"media-normalized-video-poster = A normalized video must have a poster artifact.")
            raise ValidationError(msg)
        if len(set(self.actions)) != len(self.actions):
            msg = tr(t"media-report-actions-duplicate = Normalization report actions cannot contain duplicates.")
            raise ValidationError(msg)
        if self.kind is MediaKind.IMAGE and self.output.content_type not in IMAGE_CONTENT_TYPES:
            msg = tr(t"media-normalized-images-png = Processed images must use a supported raster format.")
            raise ValidationError(msg)
        if self.kind is MediaKind.VIDEO and (
            self.output.content_type not in VIDEO_CONTENT_TYPES
            or self.poster is None
            or self.poster.content_type != "image/jpeg"
        ):
            msg = tr(
                t"media-video-format-unsupported = Processed videos must contain a supported video and JPEG thumbnail."
            )
            raise ValidationError(msg)
