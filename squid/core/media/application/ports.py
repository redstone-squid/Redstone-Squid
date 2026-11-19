"""Application ports for bounded media inspection and processing."""

from pathlib import Path
from typing import Protocol

from squid.core.media.application.commands import MediaProcessingRequest
from squid.core.media.application.models import MediaProcessingResult
from squid.core.media.domain.models import MediaLimits, MediaProbe


class MediaProcessor(Protocol):
    """Inspect and process staged files for the durable media worker."""

    async def probe(self, source_path: Path) -> MediaProbe:
        """Inspect stream metadata without decoding the complete input."""
        ...

    async def process(
        self,
        request: MediaProcessingRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaProcessingResult:
        """Create validated job-local artifacts without mutating the source.

        The caller supplies preflight metadata and limits. Implementations validate
        their outputs and remove partial files if processing fails. On success,
        the caller owns the returned files and may discard them after further checks.
        """
        ...

    async def discard(self, result: MediaProcessingResult) -> None:
        """Remove unpersisted job-local artifacts after postflight validation fails."""
        ...

    async def aclose(self) -> None:
        """Release process-level resources owned by the adapter."""
        ...
