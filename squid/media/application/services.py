"""Media normalization orchestration and cheap preflight validation."""

import stat
from dataclasses import dataclass
from pathlib import Path

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.models import MediaNormalizationResult
from squid.media.application.ports import MediaNormalizer
from squid.media.domain.models import (
    MediaBatchTotals,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaProbe,
    MediaViolation,
)
from squid.media.errors import InvalidMediaError, MediaFailureReason, MediaLimitExceededError, MediaProcessingError


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    device: int
    inode: int
    byte_size: int
    modified_nanoseconds: int


class MediaNormalizationService:
    """Validate attacker-controlled files around a tool-specific normalizer port."""

    def __init__(self, normalizer: MediaNormalizer, *, limits: MediaLimits | None = None) -> None:
        self._normalizer = normalizer
        self._limits = limits or MediaLimits()

    @property
    def limits(self) -> MediaLimits:
        """Return the limits transports should advertise before accepting bytes."""
        return self._limits

    def validate_batch(self, totals: MediaBatchTotals) -> None:
        """Validate submission-wide counts and byte reservations."""
        violation = self._limits.batch_violation(totals)
        if violation is not None:
            raise MediaLimitExceededError(violation)

    async def normalize(self, request: MediaNormalizationRequest) -> MediaNormalizationResult:
        """Probe, validate, and normalize one staged upload."""
        source = _snapshot_source(request.source_path)
        if source.byte_size > self._limits.max_source_bytes:
            raise MediaLimitExceededError(
                MediaViolation(
                    MediaLimitMeasure.SOURCE_BYTES,
                    source.byte_size,
                    self._limits.max_source_bytes,
                )
            )

        probe = await self._normalizer.probe(request.source_path)
        self._validate_probe(request.kind, probe)
        _require_unchanged(request.source_path, source)

        result = await self._normalizer.normalize(
            request,
            probe=probe,
            source_bytes=source.byte_size,
            limits=self._limits,
        )
        try:
            _require_unchanged(request.source_path, source)
            self._validate_result(request, result, probe=probe, source_bytes=source.byte_size)
        except Exception:
            await self._normalizer.discard(result)
            raise
        return result

    async def aclose(self) -> None:
        """Release normalizer resources."""
        await self._normalizer.aclose()

    def _validate_probe(self, kind: MediaKind, probe: MediaProbe) -> None:
        if kind is MediaKind.VIDEO:
            if probe.duration_milliseconds is None or probe.duration_milliseconds <= 0:
                raise InvalidMediaError(MediaFailureReason.VIDEO_DURATION_UNKNOWN)
            if probe.frame_rate_numerator <= 0:
                raise InvalidMediaError(MediaFailureReason.VIDEO_FRAME_RATE_UNKNOWN)
            if probe.width % 2 or probe.height % 2:
                raise InvalidMediaError(MediaFailureReason.VIDEO_DIMENSIONS_UNSUPPORTED)
        violation = self._limits.probe_violation(kind, probe)
        if violation is not None:
            raise MediaLimitExceededError(violation)

    def _validate_result(
        self,
        request: MediaNormalizationRequest,
        result: MediaNormalizationResult,
        *,
        probe: MediaProbe,
        source_bytes: int,
    ) -> None:
        report = result.report
        poster_bytes = report.poster.byte_size if report.poster is not None else 0
        violation = self._limits.batch_violation(MediaBatchTotals(output_bytes=report.output.byte_size + poster_bytes))
        if violation is not None:
            raise MediaLimitExceededError(violation)
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
