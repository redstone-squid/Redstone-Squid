"""Application ports for bounded media inspection and normalization."""

from pathlib import Path
from typing import Protocol

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.models import MediaNormalizationResult
from squid.media.domain.models import MediaLimits, MediaProbe


class MediaNormalizer(Protocol):
    """Tool-specific decoder/encoder behind `MediaNormalizationService`; `discard` removes one result's files.

    Implementations run each tool in an isolated subprocess and never write to the source path.
    """

    async def probe(self, source_path: Path) -> MediaProbe:
        """Read stream metadata without decoding the whole input.

        Raises:
            InvalidMediaError: The tool's output cannot be parsed into a probe.
            MediaToolUnavailableError: The tool is not installed.
            MediaProcessingTimeoutError: The tool exceeds its wall-clock deadline.
            MediaProcessingError: The tool exits non-zero.
        """
        ...

    async def normalize(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        """Write validated artifacts to the request's job-local paths.

        Raises:
            InvalidMediaError: A destination path already exists.
            MediaLimitExceededError: The artifacts exceed `limits.max_output_bytes`.
            MediaProcessingError: The tool fails or its output does not match `probe`.
            MediaToolUnavailableError: The tool is not installed.
            MediaProcessingTimeoutError: The tool exceeds its wall-clock deadline.
        """
        ...

    async def discard(self, result: MediaNormalizationResult) -> None:
        """Remove `result`'s job-local files after postflight validation fails; missing files are not an error."""
        ...

    async def aclose(self) -> None:
        """Release process-level resources held across calls, if the tool holds any."""
        ...
