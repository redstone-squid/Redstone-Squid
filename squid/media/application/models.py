"""Application-level media normalization results."""

from dataclasses import dataclass
from pathlib import Path

from squid.media.domain.models import MediaNormalizationReport


@dataclass(frozen=True, slots=True)
class MediaNormalizationResult:
    """Normalized job-local files and the deterministic report to persist."""

    output_path: Path
    poster_path: Path | None
    report: MediaNormalizationReport
