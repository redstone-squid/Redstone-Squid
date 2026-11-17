"""Requests to normalize a staged media file."""

from dataclasses import dataclass
from pathlib import Path

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.media.domain.models import MediaKind


@dataclass(frozen=True, slots=True)
class MediaNormalizationRequest:
    """Source and job-local destinations for one normalization operation.

    Videos require a poster destination for their JPEG thumbnail. Images accept
    no poster destination. Callers must provide
    distinct files; validation compares paths without resolving aliases.
    """

    kind: MediaKind
    source_path: Path
    output_path: Path
    poster_path: Path | None = None

    def __post_init__(self) -> None:
        paths = [self.source_path, self.output_path]
        if self.poster_path is not None:
            paths.append(self.poster_path)
        if len(set(paths)) != len(paths):
            msg = tr(
                t"media-media-source-normalized = Media source, normalized output, and poster paths must be distinct."
            )
            raise ValidationError(msg)
        if self.kind is MediaKind.IMAGE and self.poster_path is not None:
            msg = tr(t"media-image-not-accept = Image processing does not accept a poster path.")
            raise ValidationError(msg)
        if self.kind is MediaKind.VIDEO and self.poster_path is None:
            msg = tr(t"media-video-requires-poster = Video normalization requires a poster destination.")
            raise ValidationError(msg)
