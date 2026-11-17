"""Application-level media processing results."""

from dataclasses import dataclass
from pathlib import Path

from squid.media.domain.models import MediaProcessingReport


@dataclass(frozen=True, slots=True)
class MediaProcessingResult:
    """Normalized job-local files and their report, ready for verification and storage."""

    output_path: Path
    poster_path: Path | None
    report: MediaProcessingReport
