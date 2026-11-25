"""Media processing orchestration and cheap preflight validation."""

import stat
from dataclasses import dataclass
from pathlib import Path

from squid.core.actors import ActingContext
from squid.core.media.application.commands import MediaProcessingRequest
from squid.core.media.application.models import MediaProcessingResult
from squid.core.media.application.ports import MediaProcessor
from squid.core.media.domain.models import (
    MediaBatchTotals,
    MediaLimitMeasure,
    MediaLimits,
    MediaProbe,
    MediaViolation,
)
from squid.core.media.errors import InvalidMediaError, MediaFailureReason, MediaLimitExceededError, MediaProcessingError


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    device: int
    inode: int
    byte_size: int
    modified_nanoseconds: int


class MediaProcessingService:
    """Check source metadata and resource limits before and after processing."""

    def __init__(self, processor: MediaProcessor, *, limits: MediaLimits | None = None) -> None:
        self._processor = processor
        self._limits = limits or MediaLimits()

    @property
    def limits(self) -> MediaLimits:
        """Return the limits transports should advertise before accepting bytes."""
        return self._limits

    def validate_batch(self, totals: MediaBatchTotals) -> None:
        """Validate submission-wide counts and byte reservations."""
        if violations := self._limits.batch_violations(totals):
            raise MediaLimitExceededError(violations)

    async def process(self, request: MediaProcessingRequest, *, context: ActingContext) -> MediaProcessingResult:
        """Probe and process a regular file, then check the returned report.

        The caller must keep the source private and unchanged for this operation.
        Metadata snapshots detect replacement and ordinary writes but do not lock
        the file. The adapter validates output contents; this service checks report
        consistency and combined output size, discarding results that fail those checks.
        """
        with context.scope():
            source = _snapshot_source(request.source_path)
            if source.byte_size > self._limits.max_source_bytes:
                raise MediaLimitExceededError(
                    MediaViolation(
                        MediaLimitMeasure.SOURCE_BYTES,
                        source.byte_size,
                        self._limits.max_source_bytes,
                    )
                )

            probe = await self._processor.probe(request.source_path)
            _require_unchanged(request.source_path, source)

            result = await self._processor.process(
                request,
                probe=probe,
                source_bytes=source.byte_size,
                limits=self._limits,
            )
            try:
                _require_unchanged(request.source_path, source)
                self._validate_result(request, result, probe=probe, source_bytes=source.byte_size)
            except Exception:
                await self._processor.discard(result)
                raise
            return result

    async def aclose(self) -> None:
        """Release processor resources."""
        await self._processor.aclose()

    def _validate_result(
        self,
        request: MediaProcessingRequest,
        result: MediaProcessingResult,
        *,
        probe: MediaProbe,
        source_bytes: int,
    ) -> None:
        report = result.report
        poster_bytes = report.poster.byte_size if report.poster is not None else 0
        if violations := self._limits.batch_violations(
            MediaBatchTotals(output_bytes=report.output.byte_size + poster_bytes)
        ):
            raise MediaLimitExceededError(violations)
        if (
            result.output_path != request.output_path
            or result.poster_path != request.poster_path
            or report.kind is not request.kind
            or report.source_bytes != source_bytes
            or report.input_probe != probe
        ):
            raise MediaProcessingError(MediaFailureReason.OUTPUT_MISMATCH, operation="validate_result")


def _snapshot_source(path: Path) -> _SourceSnapshot:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InvalidMediaError(MediaFailureReason.SOURCE_NOT_REGULAR) from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise InvalidMediaError(MediaFailureReason.SOURCE_NOT_REGULAR)
    return _SourceSnapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        byte_size=metadata.st_size,
        modified_nanoseconds=metadata.st_mtime_ns,
    )


def _require_unchanged(path: Path, expected: _SourceSnapshot) -> None:
    if _snapshot_source(path) != expected:
        raise InvalidMediaError(MediaFailureReason.SOURCE_CHANGED)
