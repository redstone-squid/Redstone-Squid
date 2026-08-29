"""Media values and resource budgets, naming neither a caller nor an encoder."""

from dataclasses import dataclass
from enum import StrEnum

from squid.core.errors import ValidationError
from squid.core.i18n import tr

MEBIBYTE = 1024 * 1024


class MediaKind(StrEnum):
    """A hosted media type accepted by a submission."""

    IMAGE = "image"
    VIDEO = "video"


class MediaLimitMeasure(StrEnum):
    """Stable identifiers for capacity and decoder-work limits."""

    IMAGE_COUNT = "image_count"
    VIDEO_COUNT = "video_count"
    SOURCE_BYTES = "source_bytes"
    OUTPUT_BYTES = "output_bytes"
    DURATION_MILLISECONDS = "duration_milliseconds"
    PIXELS_PER_FRAME = "pixels_per_frame"
    DECODED_PIXELS_PER_SECOND = "decoded_pixels_per_second"


@dataclass(frozen=True, slots=True)
class MediaViolation:
    """One deterministic limit violation, suitable for an application error."""

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
            msg = tr(t"Media batch totals cannot be negative.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Centrally advertised submission and decoder-work budgets."""

    max_images: int = 10
    max_videos: int = 3
    max_duration_milliseconds: int = 5 * 60 * 1000
    max_source_bytes: int = 500 * MEBIBYTE
    max_output_bytes: int = 500 * MEBIBYTE
    max_pixels_per_frame: int = 33_200_000
    max_decoded_pixels_per_second: int = 250_000_000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_images,
                self.max_videos,
                self.max_duration_milliseconds,
                self.max_source_bytes,
                self.max_output_bytes,
                self.max_pixels_per_frame,
                self.max_decoded_pixels_per_second,
            )
            <= 0
        ):
            msg = tr(t"Every media limit must be positive.")
            raise ValidationError(msg)

    def batch_violation(self, totals: MediaBatchTotals) -> MediaViolation | None:
        """Return the first aggregate violation in a stable order."""
        checks = (
            (MediaLimitMeasure.IMAGE_COUNT, totals.image_count, self.max_images),
            (MediaLimitMeasure.VIDEO_COUNT, totals.video_count, self.max_videos),
            (MediaLimitMeasure.SOURCE_BYTES, totals.source_bytes, self.max_source_bytes),
            (MediaLimitMeasure.OUTPUT_BYTES, totals.output_bytes, self.max_output_bytes),
        )
        return next(
            (MediaViolation(measure, actual, limit) for measure, actual, limit in checks if actual > limit),
            None,
        )

    def probe_violation(self, kind: MediaKind, probe: MediaProbe) -> MediaViolation | None:
        """Return a decoded-work violation without using floating-point arithmetic."""
        checks: list[tuple[MediaLimitMeasure, int, int]] = [
            (MediaLimitMeasure.PIXELS_PER_FRAME, probe.pixels_per_frame, self.max_pixels_per_frame)
        ]
        if kind is MediaKind.VIDEO:
            if probe.duration_milliseconds is not None:
                checks.append(
                    (
                        MediaLimitMeasure.DURATION_MILLISECONDS,
                        probe.duration_milliseconds,
                        self.max_duration_milliseconds,
                    )
                )
            checks.append(
                (
                    MediaLimitMeasure.DECODED_PIXELS_PER_SECOND,
                    probe.decoded_pixels_per_second,
                    self.max_decoded_pixels_per_second,
                )
            )
        return next(
            (MediaViolation(measure, actual, limit) for measure, actual, limit in checks if actual > limit),
            None,
        )


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """Trusted stream facts returned by a bounded media inspector."""

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
            msg = tr(t"Media dimensions must be positive.")
            raise ValidationError(msg)
        if self.frame_rate_numerator < 0 or self.frame_rate_denominator <= 0:
            msg = tr(t"Media frame rate must be non-negative with a positive denominator.")
            raise ValidationError(msg)
        if self.duration_milliseconds is not None and self.duration_milliseconds < 0:
            msg = tr(t"Media duration cannot be negative.")
            raise ValidationError(msg)
        if self.frame_count is not None and self.frame_count < 0:
            msg = tr(t"Media frame count cannot be negative.")
            raise ValidationError(msg)

    @property
    def has_audio(self) -> bool:
        """Whether the inspected input contains an audio stream."""
        return self.audio_codec is not None

    @property
    def pixels_per_frame(self) -> int:
        """Return decoded pixels in one full frame."""
        return self.width * self.height

    @property
    def decoded_pixels_per_second(self) -> int:
        """Return the ceiling of width x height x average FPS."""
        numerator = self.pixels_per_frame * self.frame_rate_numerator
        return (numerator + self.frame_rate_denominator - 1) // self.frame_rate_denominator


class MediaNormalizationAction(StrEnum):
    """Stable, non-sensitive actions included in normalization reports."""

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
            msg = tr(t"Normalized artifact dimensions and size are invalid.")
            raise ValidationError(msg)
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            msg = tr(t"Normalized artifact SHA-256 must be lowercase hexadecimal.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaNormalizationReport:
    """Deterministic, persistence-safe facts from a successful normalization."""

    kind: MediaKind
    source_bytes: int
    input_probe: MediaProbe
    output_probe: MediaProbe
    output: MediaArtifact
    poster: MediaArtifact | None
    actions: tuple[MediaNormalizationAction, ...]

    def __post_init__(self) -> None:
        if self.source_bytes < 0:
            msg = tr(t"Normalized media source size cannot be negative.")
            raise ValidationError(msg)
        if self.kind is MediaKind.IMAGE and self.poster is not None:
            msg = tr(t"Only normalized videos have a poster artifact.")
            raise ValidationError(msg)
        if self.kind is MediaKind.VIDEO and self.poster is None:
            msg = tr(t"A normalized video must have a poster artifact.")
            raise ValidationError(msg)
        if len(set(self.actions)) != len(self.actions):
            msg = tr(t"Normalization report actions cannot contain duplicates.")
            raise ValidationError(msg)
        if self.kind is MediaKind.IMAGE and self.output.content_type != "image/png":
            msg = tr(t"Normalized images must be PNG artifacts.")
            raise ValidationError(msg)
        if self.kind is MediaKind.VIDEO and (
            self.output.content_type != "video/mp4" or self.poster is None or self.poster.content_type != "image/jpeg"
        ):
            msg = tr(t"Normalized videos must contain MP4 and JPEG artifacts.")
            raise ValidationError(msg)
