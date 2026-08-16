"""Bounded FFmpeg/ffprobe media normalization adapter."""

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import signal
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

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


@dataclass(frozen=True, slots=True)
class MediaProcessLimits:
    """Wall-clock and child-process guardrails for one-shot media tools."""

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
    """Normalize staged images and videos with isolated, one-shot subprocesses."""

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
                (
                    "format=format_name,duration:"
                    "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,nb_frames"
                ),
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
        return parse_ffprobe_output(stdout)

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
            return await self._normalize_image(request, probe=probe, source_bytes=source_bytes, limits=limits)
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

    async def _normalize_image(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        temporary = _temporary_destination(request.output_path, suffix=".png")
        try:
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
            _commit_one(temporary, request.output_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

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

    async def _normalize_video(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        assert request.poster_path is not None
        video_temporary = _temporary_destination(request.output_path, suffix=".mp4")
        try:
            poster_temporary = _temporary_destination(request.poster_path, suffix=".jpg")
        except Exception:
            video_temporary.unlink(missing_ok=True)
            raise
        committed_video = False
        try:
            audio_arguments = ("-an",) if request.strip_audio else ("-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k")
            await self._run(
                self._ffmpeg,
                (
                    *_ffmpeg_input_arguments(request.source_path, self._process_limits),
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
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "passthrough",
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
            _validate_video_output(probe, output_probe, strip_audio=request.strip_audio)

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
                    "-ss",
                    _format_milliseconds(seek_milliseconds),
                    "-i",
                    str(video_temporary),
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
            poster_size = _bounded_size(poster_temporary, limits.max_output_bytes)
            _validate_combined_size(video_size, poster_size, limit=limits.max_output_bytes)
            poster_probe = await self.probe(poster_temporary)
            _validate_poster_output(poster_probe)

            output_artifact = _artifact(video_temporary, "video/mp4", output_probe, byte_size=video_size)
            poster_artifact = _artifact(poster_temporary, "image/jpeg", poster_probe, byte_size=poster_size)
            _commit_one(video_temporary, request.output_path)
            committed_video = True
            _commit_one(poster_temporary, request.poster_path)
        except Exception:
            video_temporary.unlink(missing_ok=True)
            poster_temporary.unlink(missing_ok=True)
            if committed_video:
                request.output_path.unlink(missing_ok=True)
            raise

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
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        except TimeoutError as exc:
            await _terminate(process)
            raise MediaProcessingTimeoutError(operation=operation) from exc
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
    """Translate bounded ffprobe JSON into a deterministic domain value."""
    try:
        payload = cast(Mapping[str, Any], json.loads(data))
        streams = cast(list[Mapping[str, Any]], payload["streams"])
        format_data = cast(Mapping[str, Any], payload.get("format", {}))
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        width = _positive_int(video["width"])
        height = _positive_int(video["height"])
        frame_rate = _frame_rate(video)
        duration_raw = video.get("duration")
        if duration_raw in {None, "N/A"}:
            duration_raw = format_data.get("duration")
        duration = _duration_milliseconds(duration_raw)
        frame_count = _optional_non_negative_int(video.get("nb_frames"))
        video_codec = _short_string(video["codec_name"])
        audio_codec = _short_string(audio["codec_name"]) if audio is not None else None
        container = _short_string(format_data.get("format_name", "unknown"))
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
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


def _ffmpeg_input_arguments(source_path: Path, limits: MediaProcessLimits) -> tuple[str, ...]:
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
        "-i",
        str(source_path.absolute()),
    )


def _validate_video_output(source: MediaProbe, output: MediaProbe, *, strip_audio: bool) -> None:
    expected_audio = source.has_audio and not strip_audio
    duration_tolerance = max(250, _frame_duration_milliseconds(source) * 2)
    if (
        output.video_codec != "h264"
        or "mp4" not in output.container_names
        or output.width != source.width
        or output.height != source.height
        or output.frame_rate_numerator * source.frame_rate_denominator
        != source.frame_rate_numerator * output.frame_rate_denominator
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


def _frame_rate(stream: Mapping[str, Any]) -> Fraction:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
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
        if os.name == "posix" and process.pid is not None:
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercised on Windows CI
            process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


def _apply_process_limits(limits: MediaProcessLimits, *, max_file_bytes: int) -> None:
    if resource is None:  # pragma: no cover - Windows has no rlimits
        return
    _set_resource_limit(resource.RLIMIT_AS, limits.memory_bytes)
    _set_resource_limit(resource.RLIMIT_CPU, limits.cpu_seconds)
    _set_resource_limit(resource.RLIMIT_FSIZE, max_file_bytes)
    _set_resource_limit(resource.RLIMIT_NOFILE, limits.max_open_files)
    _set_resource_limit(resource.RLIMIT_CORE, 0)
    with contextlib.suppress(OSError):
        os.nice(5)


def _set_resource_limit(which: int, soft: int) -> None:
    assert resource is not None
    try:
        _, hard = resource.getrlimit(which)
        ceiling = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
        resource.setrlimit(which, (ceiling, hard))
    except OSError, ValueError:
        pass
