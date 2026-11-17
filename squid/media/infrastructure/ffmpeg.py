"""Bounded FFmpeg/ffprobe media normalization adapter."""

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any

from anyio import CancelScope, fail_after
from pydantic import BaseModel, ConfigDict, ValidationError

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no rlimits
    resource = None  # type: ignore[assignment]

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.models import MediaNormalizationResult
from squid.media.domain.models import (
    MediaArtifact,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaNormalizationAction,
    MediaNormalizationReport,
    MediaProbe,
    MediaViolation,
)
from squid.media.errors import (
    InvalidMediaError,
    MediaFailureReason,
    MediaLimitExceededError,
    MediaProcessingError,
    MediaProcessingTimeoutError,
    MediaToolUnavailableError,
)

_NETWORK_PROTOCOLS = "http,https,tcp,tls,udp,rtmp,rtmps,rtp,rtsp,sftp,ftp"
_INPUT_PROTOCOLS = "file,pipe"
_MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
_TIMESTAMP_TOLERANCE = Decimal("0.001")


class _FfprobeJson(BaseModel):
    # ffprobe owns these shapes and may add fields in later releases.
    model_config = ConfigDict(extra="ignore", strict=True)


class _ProbeSideDataJson(_FfprobeJson):
    rotation: str | int | float | None = None


class _ProbeStreamJson(_FfprobeJson):
    codec_type: str
    codec_name: str | None = None
    width: int | str | None = None
    height: int | str | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    duration: str | None = None
    nb_frames: str | int | None = None
    side_data_list: tuple[_ProbeSideDataJson, ...] = ()


class _ProbeFormatJson(_FfprobeJson):
    format_name: str = "unknown"
    duration: str | None = None


class _ProbeJson(_FfprobeJson):
    streams: tuple[_ProbeStreamJson, ...]
    format: _ProbeFormatJson = _ProbeFormatJson()


class _BrandTagsJson(_FfprobeJson):
    major_brand: str = ""


class _BrandFormatJson(_FfprobeJson):
    tags: _BrandTagsJson = _BrandTagsJson()


class _BrandProbeJson(_FfprobeJson):
    format: _BrandFormatJson = _BrandFormatJson()


class _ColorStreamJson(_FfprobeJson):
    codec_type: str
    color_space: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None


class _ColorProbeJson(_FfprobeJson):
    streams: tuple[_ColorStreamJson, ...]


class _FrameTimestampJson(_FfprobeJson):
    best_effort_timestamp_time: str


class _FrameTimestampsJson(_FfprobeJson):
    frames: tuple[_FrameTimestampJson, ...]


class _FrameCountStreamJson(_FfprobeJson):
    nb_read_frames: str | int | None = None


class _FrameCountJson(_FfprobeJson):
    streams: tuple[_FrameCountStreamJson, ...]


@dataclass(frozen=True, slots=True)
class MediaProcessLimits:
    """Timeouts, thread counts, and requested OS resource limits for each media process.

    Resource limits are best effort on POSIX and unavailable on Windows. Wall-clock
    timeouts apply on both platforms.
    """

    probe_timeout_seconds: float = 15.0
    image_timeout_seconds: float = 120.0
    video_timeout_seconds: float = 600.0
    poster_timeout_seconds: float = 120.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    cpu_seconds: int = 540
    max_open_files: int = 128
    threads: int = 2

    def __post_init__(self) -> None:
        if (
            min(
                self.probe_timeout_seconds,
                self.image_timeout_seconds,
                self.video_timeout_seconds,
                self.poster_timeout_seconds,
                self.memory_bytes,
                self.cpu_seconds,
                self.max_open_files,
                self.threads,
            )
            <= 0
        ):
            msg = "Every media process limit must be positive."
            raise ValueError(msg)


class FfmpegMediaNormalizer:
    """Preserve raster images and videos, generating a JPEG thumbnail for videos.

    Each probe or encoding step runs in a separate subprocess. Outputs are staged
    beside their destinations and published only after size and stream checks pass.
    """

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        process_limits: MediaProcessLimits | None = None,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._process_limits = process_limits or MediaProcessLimits()

    async def probe(self, source_path: Path) -> MediaProbe:
        """Inspect the first video stream and optional first audio stream."""
        return parse_ffprobe_output(
            await self._probe_output(
                source_path,
                "format=format_name,duration:"
                "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,nb_frames:"
                "stream_side_data=rotation",
            )
        )

    async def _probe_output(self, source_path: Path, entries: str) -> bytes:
        stdout = await self._run(
            self._ffprobe,
            (
                "-v",
                "error",
                "-hide_banner",
                "-protocol_whitelist",
                _INPUT_PROTOCOLS,
                "-protocol_blacklist",
                _NETWORK_PROTOCOLS,
                "-probesize",
                str(32 * 1024 * 1024),
                "-analyzeduration",
                "10000000",
                "-show_entries",
                entries,
                "-of",
                "json",
                str(source_path.absolute()),
            ),
            operation="probe",
            timeout_seconds=self._process_limits.probe_timeout_seconds,
            max_file_bytes=_MAX_PROBE_OUTPUT_BYTES,
            capture_stdout=True,
        )
        if len(stdout) > _MAX_PROBE_OUTPUT_BYTES:
            raise InvalidMediaError(MediaFailureReason.PROBE_INVALID)
        return stdout

    async def normalize(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        """Produce normalized artifacts, committing them only after validation."""
        if request.kind is MediaKind.IMAGE:
            if probe.original_image_content_type is not None:
                return await self._preserve_image(request, probe=probe, source_bytes=source_bytes, limits=limits)
            return await self._normalize_image(request, probe=probe, source_bytes=source_bytes, limits=limits)
        if not request.strip_audio:
            return await self._preserve_video(request, probe=probe, source_bytes=source_bytes, limits=limits)
        return await self._normalize_video(request, probe=probe, source_bytes=source_bytes, limits=limits)

    async def aclose(self) -> None:
        """One-shot FFmpeg processes leave no reusable resources."""

    async def discard(self, result: MediaNormalizationResult) -> None:
        """Remove job-local outputs after an application postflight failure."""
        try:
            result.output_path.unlink(missing_ok=True)
            if result.poster_path is not None:
                result.poster_path.unlink(missing_ok=True)
        except OSError as exc:
            raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="discard_output") from exc

    async def _preserve_image(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        assert probe.original_image_content_type is not None
        with _staged_outputs((request.output_path, ".image")) as (temporary,):
            with CancelScope(shield=True):
                await asyncio.to_thread(shutil.copyfile, request.source_path, temporary)
            size = _bounded_size(temporary, limits.max_output_bytes)
            artifact = _artifact(temporary, probe.original_image_content_type, probe, byte_size=size)
        return MediaNormalizationResult(
            output_path=request.output_path,
            poster_path=None,
            report=MediaNormalizationReport(
                kind=MediaKind.IMAGE,
                source_bytes=source_bytes,
                input_probe=probe,
                output_probe=probe,
                output=artifact,
                poster=None,
                actions=(),
            ),
        )

    async def _normalize_image(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        with _staged_outputs((request.output_path, ".png")) as (temporary,):
            await self._run(
                self._ffmpeg,
                (
                    *_ffmpeg_input_arguments(request.source_path, self._process_limits),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                    "-c:v",
                    "png",
                    "-threads",
                    str(self._process_limits.threads),
                    "-pix_fmt",
                    "rgba",
                    "-fflags",
                    "+bitexact",
                    "-flags:v",
                    "+bitexact",
                    "-update",
                    "1",
                    "-f",
                    "image2",
                    str(temporary),
                ),
                operation="normalize_image",
                timeout_seconds=self._process_limits.image_timeout_seconds,
                max_file_bytes=limits.max_output_bytes,
            )
            output_size = _bounded_size(temporary, limits.max_output_bytes)
            output_probe = await self.probe(temporary)
            _validate_image_output(probe, output_probe)
            artifact = _artifact(temporary, "image/png", output_probe, byte_size=output_size)

        report = MediaNormalizationReport(
            kind=MediaKind.IMAGE,
            source_bytes=source_bytes,
            input_probe=probe,
            output_probe=output_probe,
            output=artifact,
            poster=None,
            actions=(
                MediaNormalizationAction.METADATA_REMOVED,
                MediaNormalizationAction.IMAGE_REENCODED,
            ),
        )
        return MediaNormalizationResult(output_path=request.output_path, poster_path=None, report=report)

    async def _preserve_video(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        assert request.poster_path is not None
        containers = set(probe.container_names)
        if "mp4" in containers:
            metadata = _BrandProbeJson.model_validate_json(
                await self._probe_output(request.source_path, "format_tags=major_brand")
            )
            brand = metadata.format.tags.major_brand
            content_type = "video/quicktime" if brand.strip() == "qt" else "video/mp4"
        elif "webm" in containers:
            content_type = "video/webm" if probe.video_codec in {"vp8", "vp9", "av1"} else "video/x-matroska"
        elif "matroska" in containers:
            content_type = "video/x-matroska"
        elif "avi" in containers:
            content_type = "video/x-msvideo"
        elif "mpeg" in containers:
            content_type = "video/mpeg"
        else:
            raise InvalidMediaError(MediaFailureReason.PROBE_INVALID)
        with _staged_outputs((request.output_path, ".video"), (request.poster_path, ".jpg")) as (video, poster):
            with CancelScope(shield=True):
                await asyncio.to_thread(shutil.copyfile, request.source_path, video)
            video_size = _bounded_size(video, limits.max_output_bytes)
            await self._generate_poster(video, poster, probe, limits)
            poster_size = _bounded_size(poster, limits.max_output_bytes)
            _validate_combined_size(video_size, poster_size, limit=limits.max_output_bytes)
            poster_probe = await self.probe(poster)
            _validate_poster_output(poster_probe)
            output = _artifact(video, content_type, probe, byte_size=video_size)
            thumbnail = _artifact(poster, "image/jpeg", poster_probe, byte_size=poster_size)
        actions = [MediaNormalizationAction.POSTER_GENERATED]
        if probe.has_audio:
            actions.append(MediaNormalizationAction.AUDIO_PRESERVED)
        return MediaNormalizationResult(
            output_path=request.output_path,
            poster_path=request.poster_path,
            report=MediaNormalizationReport(
                kind=MediaKind.VIDEO,
                source_bytes=source_bytes,
                input_probe=probe,
                output_probe=probe,
                output=output,
                poster=thumbnail,
                actions=tuple(actions),
            ),
        )

    async def _generate_poster(
        self, source_path: Path, poster_temporary: Path, probe: MediaProbe, limits: MediaLimits
    ) -> None:
        color_arguments = await self._video_color_arguments(source_path, probe)
        seek_milliseconds = min((probe.duration_milliseconds or 0) // 2, 30_000)
        await self._run(
            self._ffmpeg,
            (
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-xerror",
                "-protocol_whitelist",
                _INPUT_PROTOCOLS,
                "-protocol_blacklist",
                _NETWORK_PROTOCOLS,
                "-threads",
                str(self._process_limits.threads),
                "-filter_threads",
                str(self._process_limits.threads),
                *color_arguments,
                "-ss",
                _format_milliseconds(seek_milliseconds),
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                "scale=w='min(1920,iw)':h=-2",
                "-map_metadata",
                "-1",
                "-c:v",
                "mjpeg",
                "-threads",
                str(self._process_limits.threads),
                "-q:v",
                "2",
                "-pix_fmt",
                "yuvj420p",
                "-fflags",
                "+bitexact",
                "-flags:v",
                "+bitexact",
                "-update",
                "1",
                "-f",
                "image2",
                str(poster_temporary),
            ),
            operation="generate_poster",
            timeout_seconds=self._process_limits.poster_timeout_seconds,
            max_file_bytes=limits.max_output_bytes,
        )

    async def _video_color_arguments(self, source_path: Path, probe: MediaProbe) -> tuple[str, ...]:
        if probe.video_codec != "h264":
            return ()
        data = _ColorProbeJson.model_validate_json(
            await self._probe_output(source_path, "stream=codec_type,color_space,color_primaries,color_transfer")
        )
        stream = next(stream for stream in data.streams if stream.codec_type == "video")
        replacements = [
            f"{option}=2"
            for field, option in (
                ("color_space", "matrix_coefficients"),
                ("color_primaries", "colour_primaries"),
                ("color_transfer", "transfer_characteristics"),
            )
            if getattr(stream, field) == "reserved"
        ]
        # Reserved H.264 VUI values break FFmpeg's color conversion. Mark only
        # those fields unspecified before decoding, preserving valid color tags.
        # Remove padding before the metadata parser: some recorders emit filler
        # packets without a trailing stop bit. Picture and timing data stay intact.
        return (
            ("-bsf:v:0", "filter_units=remove_types=12,h264_metadata=" + ":".join(replacements)) if replacements else ()
        )

    async def _normalize_video(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        assert request.poster_path is not None
        color_arguments = await self._video_color_arguments(request.source_path, probe)
        with _staged_outputs((request.output_path, ".mp4"), (request.poster_path, ".jpg")) as (
            video_temporary,
            poster_temporary,
        ):
            audio_arguments = ("-an",) if request.strip_audio else ("-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k")
            # A frame-rate-based encoder clock can merge closely spaced VFR timestamps.
            # Keep the demuxer's precision as well as passing each frame through.
            await self._run(
                self._ffmpeg,
                (
                    *_ffmpeg_input_arguments(request.source_path, self._process_limits, color_arguments),
                    "-map",
                    "0:v:0",
                    *audio_arguments,
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                    "-c:v",
                    "libx264",
                    "-threads",
                    str(self._process_limits.threads),
                    "-preset",
                    "medium",
                    # Bound queued frames so 4K clips fit the process memory limit.
                    "-rc-lookahead",
                    "8",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "passthrough",
                    "-enc_time_base:v",
                    "demux",
                    "-movflags",
                    "+faststart",
                    "-fflags",
                    "+bitexact",
                    "-flags:v",
                    "+bitexact",
                    "-f",
                    "mp4",
                    str(video_temporary),
                ),
                operation="normalize_video",
                timeout_seconds=self._process_limits.video_timeout_seconds,
                max_file_bytes=limits.max_output_bytes,
            )
            video_size = _bounded_size(video_temporary, limits.max_output_bytes)
            output_probe = await self.probe(video_temporary)
            validation_source, validation_output = probe, output_probe
            if (
                probe.frame_count is not None
                and output_probe.frame_count is not None
                and probe.frame_count != output_probe.frame_count
            ):
                # MP4 sample counts can include frames the demuxer does not expose.
                # Count decoded frames before treating a header mismatch as frame loss.
                validation_source = replace(probe, frame_count=await self._decoded_frame_count(request.source_path))
                validation_output = replace(output_probe, frame_count=await self._decoded_frame_count(video_temporary))
            timestamps_verified = False
            if _average_frame_rate_changed(validation_source, validation_output):
                source_timestamps = await self._frame_timestamps(request.source_path)
                output_timestamps = await self._frame_timestamps(video_temporary)
                if len(source_timestamps) == len(output_timestamps):
                    offset = output_timestamps[0] - source_timestamps[0]
                    timestamps_verified = all(
                        abs(output_time - source_time - offset) <= _TIMESTAMP_TOLERANCE
                        for source_time, output_time in zip(source_timestamps, output_timestamps, strict=True)
                    )
            _validate_video_output(
                validation_source,
                validation_output,
                strip_audio=request.strip_audio,
                timestamps_verified=timestamps_verified,
            )

            await self._generate_poster(video_temporary, poster_temporary, output_probe, limits)
            poster_size = _bounded_size(poster_temporary, limits.max_output_bytes)
            _validate_combined_size(video_size, poster_size, limit=limits.max_output_bytes)
            poster_probe = await self.probe(poster_temporary)
            _validate_poster_output(poster_probe)

            output_artifact = _artifact(video_temporary, "video/mp4", output_probe, byte_size=video_size)
            poster_artifact = _artifact(poster_temporary, "image/jpeg", poster_probe, byte_size=poster_size)

        actions = [
            MediaNormalizationAction.METADATA_REMOVED,
            MediaNormalizationAction.VIDEO_TRANSCODED,
        ]
        if probe.has_audio:
            actions.append(
                MediaNormalizationAction.AUDIO_REMOVED
                if request.strip_audio
                else MediaNormalizationAction.AUDIO_PRESERVED
            )
        actions.append(MediaNormalizationAction.POSTER_GENERATED)
        report = MediaNormalizationReport(
            kind=MediaKind.VIDEO,
            source_bytes=source_bytes,
            input_probe=probe,
            output_probe=output_probe,
            output=output_artifact,
            poster=poster_artifact,
            actions=tuple(actions),
        )
        return MediaNormalizationResult(
            output_path=request.output_path,
            poster_path=request.poster_path,
            report=report,
        )

    async def _frame_timestamps(self, source_path: Path) -> tuple[Decimal, ...]:
        """Read decoded presentation times when average-rate metadata cannot establish preservation."""
        stdout = await self._run(
            self._ffprobe,
            (
                "-v",
                "error",
                "-protocol_whitelist",
                _INPUT_PROTOCOLS,
                "-protocol_blacklist",
                _NETWORK_PROTOCOLS,
                "-threads",
                str(self._process_limits.threads),
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(source_path.absolute()),
            ),
            operation="frame_timestamps",
            timeout_seconds=self._process_limits.video_timeout_seconds,
            max_file_bytes=8 * _MAX_PROBE_OUTPUT_BYTES,
            capture_stdout=True,
        )
        try:
            payload = _FrameTimestampsJson.model_validate_json(stdout)
            timestamps = tuple(Decimal(frame.best_effort_timestamp_time) for frame in payload.frames)
            if (
                timestamps
                and all(value.is_finite() for value in timestamps)
                # Decoder estimates can regress by less than a millisecond at EOF.
                # Keep every value unchanged for the per-frame preservation check.
                and all(later >= earlier - _TIMESTAMP_TOLERANCE for earlier, later in pairwise(timestamps))
            ):
                return timestamps
        except TypeError, ValueError, InvalidOperation, ValidationError:
            pass
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="frame_timestamps")

    async def _decoded_frame_count(self, source_path: Path) -> int:
        """Count decoded video frames when container counts disagree after normalization."""
        stdout = await self._run(
            self._ffprobe,
            (
                "-v",
                "error",
                "-protocol_whitelist",
                _INPUT_PROTOCOLS,
                "-protocol_blacklist",
                _NETWORK_PROTOCOLS,
                "-threads",
                str(self._process_limits.threads),
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "json",
                str(source_path.absolute()),
            ),
            operation="count_frames",
            timeout_seconds=self._process_limits.video_timeout_seconds,
            max_file_bytes=_MAX_PROBE_OUTPUT_BYTES,
            capture_stdout=True,
        )
        try:
            payload = _FrameCountJson.model_validate_json(stdout)
            frame_count = _optional_non_negative_int(payload.streams[0].nb_read_frames)
            if frame_count is not None and frame_count > 0:
                return frame_count
        except IndexError, TypeError, ValueError, ValidationError:
            pass
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="count_frames")

    async def _run(
        self,
        executable: str,
        arguments: Sequence[str],
        *,
        operation: str,
        timeout_seconds: float,
        max_file_bytes: int,
        capture_stdout: bool = False,
    ) -> bytes:
        resolved = shutil.which(executable)
        if resolved is None:
            raise MediaToolUnavailableError(tool=executable)

        process_arguments: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.PIPE,
            "env": {"LANG": "C", "LC_ALL": "C", "AV_LOG_FORCE_NOCOLOR": "1"},
        }
        if os.name == "posix":
            process_arguments["start_new_session"] = True
            process_arguments["preexec_fn"] = lambda: _apply_process_limits(
                self._process_limits,
                max_file_bytes=max_file_bytes,
            )
        try:
            process = await asyncio.create_subprocess_exec(resolved, *arguments, **process_arguments)
        except OSError as exc:
            raise MediaToolUnavailableError(tool=executable) from exc

        try:
            try:
                with fail_after(timeout_seconds):
                    stdout, _stderr = await process.communicate()
            except TimeoutError as exc:
                raise MediaProcessingTimeoutError(operation=operation) from exc
        except BaseException:
            # The owning task may already be cancelled; reaping must still settle before it exits.
            with CancelScope(shield=True):
                await _terminate(process)
            raise
        if process.returncode != 0:
            if process.returncode == -getattr(signal, "SIGXFSZ", 25):
                raise MediaLimitExceededError(
                    MediaViolation(MediaLimitMeasure.OUTPUT_BYTES, max_file_bytes + 1, max_file_bytes)
                )
            raise MediaProcessingError(
                MediaFailureReason.TOOL_FAILED,
                operation=operation,
                exit_code=process.returncode,
            )
        return stdout or b""


def parse_ffprobe_output(data: bytes) -> MediaProbe:
    """Read the first video and audio streams from ffprobe JSON.

    Dimensions include quarter-turn display rotation, matching FFmpeg's autorotation.
    Durations are rounded up to milliseconds, falling back to container duration
    when the video stream omits it. Missing frame rates become zero. Malformed
    required metadata raises InvalidMediaError; the caller bounds the input size.
    """
    try:
        payload = _ProbeJson.model_validate_json(data)
        video = next(stream for stream in payload.streams if stream.codec_type == "video")
        audio = next((stream for stream in payload.streams if stream.codec_type == "audio"), None)
        width, height = _display_dimensions(video)
        frame_rate = _frame_rate(video)
        duration_raw = video.duration
        if duration_raw in {None, "N/A"}:
            duration_raw = payload.format.duration
        duration = _duration_milliseconds(duration_raw)
        frame_count = _optional_non_negative_int(video.nb_frames)
        video_codec = _short_string(video.codec_name)
        audio_codec = _short_string(audio.codec_name) if audio is not None else None
        container = _short_string(payload.format.format_name)
    except (
        AttributeError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
        InvalidOperation,
        ValidationError,
    ) as exc:
        raise InvalidMediaError(MediaFailureReason.PROBE_INVALID) from exc
    return MediaProbe(
        container_names=tuple(name for name in container.split(",") if name),
        video_codec=video_codec,
        width=width,
        height=height,
        frame_rate_numerator=frame_rate.numerator,
        frame_rate_denominator=frame_rate.denominator,
        duration_milliseconds=duration,
        audio_codec=audio_codec,
        frame_count=frame_count,
    )


def _display_dimensions(video: _ProbeStreamJson) -> tuple[int, int]:
    width, height = _positive_int(video.width), _positive_int(video.height)
    rotation = next(
        (Decimal(str(side.rotation)) for side in video.side_data_list if side.rotation is not None),
        Decimal(0),
    )
    if not rotation.is_finite():
        msg = "Display rotation must be finite"
        raise ValueError(msg)
    return (height, width) if abs(rotation) % 180 == 90 else (width, height)


def _ffmpeg_input_arguments(
    source_path: Path, limits: MediaProcessLimits, stream_arguments: tuple[str, ...] = ()
) -> tuple[str, ...]:
    return (
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-xerror",
        "-protocol_whitelist",
        _INPUT_PROTOCOLS,
        "-protocol_blacklist",
        _NETWORK_PROTOCOLS,
        "-threads",
        str(limits.threads),
        "-filter_threads",
        str(limits.threads),
        "-filter_complex_threads",
        str(limits.threads),
        *stream_arguments,
        "-i",
        str(source_path.absolute()),
    )


def _average_frame_rate_changed(source: MediaProbe, output: MediaProbe) -> bool:
    # Average rates depend on the muxer's first/last sample durations. Allow at most
    # one frame of rate drift over the common interval; probe durations round up to milliseconds.
    rate_difference = abs(
        Fraction(output.frame_rate_numerator, output.frame_rate_denominator)
        - Fraction(source.frame_rate_numerator, source.frame_rate_denominator)
    )
    clip_duration = max(1, min(source.duration_milliseconds or 0, output.duration_milliseconds or 0) - 1)
    return rate_difference * clip_duration > 1000


def _validate_video_output(
    source: MediaProbe, output: MediaProbe, *, strip_audio: bool, timestamps_verified: bool = False
) -> None:
    expected_audio = source.has_audio and not strip_audio
    duration_tolerance = max(250, _frame_duration_milliseconds(source) * 2)
    if (
        output.video_codec != "h264"
        or "mp4" not in output.container_names
        or output.width != source.width
        or output.height != source.height
        or output.frame_rate_numerator <= 0
        or (not timestamps_verified and _average_frame_rate_changed(source, output))
        or output.has_audio != expected_audio
        or (output.has_audio and output.audio_codec != "aac")
        or output.duration_milliseconds is None
        or source.duration_milliseconds is None
        or abs(output.duration_milliseconds - source.duration_milliseconds) > duration_tolerance
        or (
            source.frame_count is not None
            and output.frame_count is not None
            and source.frame_count != output.frame_count
        )
    ):
        raise MediaProcessingError(MediaFailureReason.OUTPUT_MISMATCH, operation="validate_video")


def _validate_image_output(source: MediaProbe, output: MediaProbe) -> None:
    if (
        output.video_codec != "png"
        or output.width != source.width
        or output.height != source.height
        or output.has_audio
    ):
        raise MediaProcessingError(MediaFailureReason.OUTPUT_MISMATCH, operation="validate_image")


def _validate_poster_output(output: MediaProbe) -> None:
    if output.video_codec != "mjpeg" or output.has_audio:
        raise MediaProcessingError(MediaFailureReason.OUTPUT_MISMATCH, operation="validate_poster")


def _validate_combined_size(video_size: int, poster_size: int, *, limit: int) -> None:
    combined_size = video_size + poster_size
    if combined_size > limit:
        raise MediaLimitExceededError(MediaViolation(MediaLimitMeasure.OUTPUT_BYTES, combined_size, limit))


@contextlib.contextmanager
def _staged_outputs(*destinations: tuple[Path, str]) -> Iterator[tuple[Path, ...]]:
    """Yield temporary paths and publish them when the body exits successfully.

    Destinations must not already exist. Each file is published with a hard link;
    if a later publication fails, earlier destinations are removed. This provides
    rollback on failure, not atomic visibility of the whole set.
    """
    with contextlib.ExitStack() as temporary_cleanup:
        staged: list[Path] = []
        for destination, suffix in destinations:
            temporary = _temporary_destination(destination, suffix=suffix)
            temporary_cleanup.callback(temporary.unlink, missing_ok=True)
            staged.append(temporary)
        yield tuple(staged)
        with contextlib.ExitStack() as rollback:
            for temporary, (destination, _) in zip(staged, destinations, strict=True):
                _commit_one(temporary, destination)
                rollback.callback(destination.unlink, missing_ok=True)
            rollback.pop_all()


def _temporary_destination(destination: Path, *, suffix: str) -> Path:
    if destination.exists() or destination.is_symlink():
        raise InvalidMediaError(MediaFailureReason.OUTPUT_EXISTS)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".normalizing-", suffix=suffix, dir=destination.parent)
        os.close(descriptor)
    except OSError as exc:
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="prepare_output") from exc
    return Path(name)


def _commit_one(temporary: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise InvalidMediaError(MediaFailureReason.OUTPUT_EXISTS)
    linked = False
    try:
        # Link creation fails if another writer created the destination after the check.
        destination.hardlink_to(temporary)
        linked = True
        temporary.unlink()
    except OSError as exc:
        if linked:
            destination.unlink(missing_ok=True)
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="commit_output") from exc


def _bounded_size(path: Path, limit: int) -> int:
    try:
        byte_size = path.stat().st_size
    except OSError as exc:
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="stat_output") from exc
    if byte_size > limit:
        raise MediaLimitExceededError(MediaViolation(MediaLimitMeasure.OUTPUT_BYTES, byte_size, limit))
    if byte_size == 0:
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="stat_output")
    return byte_size


def _artifact(path: Path, content_type: str, probe: MediaProbe, *, byte_size: int) -> MediaArtifact:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="hash_output") from exc
    return MediaArtifact(
        content_type=content_type,
        byte_size=byte_size,
        sha256=digest.hexdigest(),
        width=probe.width,
        height=probe.height,
    )


def _frame_rate(stream: _ProbeStreamJson) -> Fraction:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = getattr(stream, key)
        if not isinstance(raw, str) or len(raw) > 32:
            continue
        try:
            rate = Fraction(raw)
        except ValueError, ZeroDivisionError:
            continue
        if rate >= 0:
            return rate
    return Fraction(0, 1)


def _duration_milliseconds(raw: Any) -> int | None:
    if raw in {None, "N/A"}:
        return None
    try:
        seconds = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError
    return int((seconds * 1000).to_integral_value(rounding=ROUND_CEILING))


def _positive_int(raw: Any) -> int:
    value = int(raw)
    if isinstance(raw, bool) or value <= 0:
        raise ValueError
    return value


def _optional_non_negative_int(raw: Any) -> int | None:
    if raw in {None, "N/A"}:
        return None
    value = int(raw)
    if isinstance(raw, bool) or value < 0:
        raise ValueError
    return value


def _short_string(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 256:
        raise ValueError
    return raw


def _format_milliseconds(value: int) -> str:
    return f"{value // 1000}.{value % 1000:03d}"


def _frame_duration_milliseconds(probe: MediaProbe) -> int:
    if probe.frame_rate_numerator <= 0:
        return 0
    numerator = 1000 * probe.frame_rate_denominator
    return (numerator + probe.frame_rate_numerator - 1) // probe.frame_rate_numerator


async def _terminate(process: asyncio.subprocess.Process) -> None:
    try:
        # typeshed spells Process.pid as int, but it is whatever the transport's get_pid()
        # returned, and that is None until the transport has actually spawned the child.
        if os.name == "posix" and process.pid is not None:  # pyright: ignore[reportUnnecessaryComparison]
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercised on Windows CI
            process.kill()
    except ProcessLookupError:
        pass
    # A cancelled communicate() can leave paused pipe readers. Drain them so wait() can finish.
    await process.communicate()


def _apply_process_limits(limits: MediaProcessLimits, *, max_file_bytes: int) -> None:
    if resource is None:  # pragma: no cover - Windows has no rlimits
        return
    _set_resource_limit(resource.RLIMIT_AS, limits.memory_bytes)  # pyrefly: ignore[missing-attribute]
    _set_resource_limit(resource.RLIMIT_CPU, limits.cpu_seconds)  # pyrefly: ignore[missing-attribute]
    _set_resource_limit(resource.RLIMIT_FSIZE, max_file_bytes)  # pyrefly: ignore[missing-attribute]
    _set_resource_limit(resource.RLIMIT_NOFILE, limits.max_open_files)  # pyrefly: ignore[missing-attribute]
    _set_resource_limit(resource.RLIMIT_CORE, 0)  # pyrefly: ignore[missing-attribute]
    with contextlib.suppress(OSError):
        os.nice(5)  # pyrefly: ignore[missing-attribute]


def _set_resource_limit(which: int, soft: int) -> None:
    assert resource is not None
    try:
        _, hard = resource.getrlimit(which)  # pyrefly: ignore[missing-attribute]
        ceiling = soft if hard == resource.RLIM_INFINITY else min(soft, hard)  # pyrefly: ignore[missing-attribute]
        resource.setrlimit(which, (ceiling, hard))  # pyrefly: ignore[missing-attribute]
    except OSError, ValueError:
        pass
