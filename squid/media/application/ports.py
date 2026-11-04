"""Application ports for bounded media inspection and normalization."""

from pathlib import Path
from typing import Protocol

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.models import MediaNormalizationResult
from squid.media.domain.models import MediaLimits, MediaProbe


class MediaNormalizer(Protocol):
    """Inspect and normalize staged files for the durable media worker."""

    async def probe(self, source_path: Path) -> MediaProbe:
        """Inspect stream metadata without decoding the complete input."""
        ...

    async def normalize(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        """Create validated job-local artifacts without mutating the source.

        The caller supplies preflight metadata and limits. Implementations validate
        their outputs and remove partial files if normalization fails. On success,
        the caller owns the returned files and may discard them after further checks.
        """
        ...

    async def discard(self, result: MediaNormalizationResult) -> None:
        """Remove unpersisted job-local artifacts after postflight validation fails."""
        ...

    async def aclose(self) -> None:
        """Release process-level resources owned by the adapter."""
        ...
