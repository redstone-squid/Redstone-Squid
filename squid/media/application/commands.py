"""Media normalization requests, built the same way by the bot, the API, and the worker."""

from dataclasses import dataclass
from pathlib import Path

from squid.media.domain.models import MediaKind


@dataclass(frozen=True, slots=True)
class MediaNormalizationRequest:
    """Source and job-local destinations for one normalization operation."""

    kind: MediaKind
    source_path: Path
    output_path: Path
    poster_path: Path | None = None
    strip_audio: bool = False

    def __post_init__(self) -> None:
        paths = [self.source_path, self.output_path]
        if self.poster_path is not None:
            paths.append(self.poster_path)
        if len(set(paths)) != len(paths):
            msg = "Media source, normalized output, and poster paths must be distinct."
            raise ValueError(msg)
        if self.kind is MediaKind.IMAGE and (self.poster_path is not None or self.strip_audio):
            msg = "Image normalization does not accept a poster path or audio option."
            raise ValueError(msg)
        if self.kind is MediaKind.VIDEO and self.poster_path is None:
            msg = "Video normalization requires a poster destination."
            raise ValueError(msg)
