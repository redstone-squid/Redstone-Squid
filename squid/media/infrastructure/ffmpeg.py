"""Bounded FFmpeg/ffprobe media processing adapter."""

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

import ffmpeg
from anyio import CancelScope, fail_after
from ffmpeg.dag.nodes import OutputStream
from ffmpeg.options.codec import decoder_codec_context, encoder_codec_context
from ffmpeg.options.format import encoder_format_context
from pydantic import BaseModel, ConfigDict, ValidationError

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no rlimits
    resource = None  # type: ignore[assignment]

from squid.media.application.commands import MediaProcessingRequest
from squid.media.application.models import MediaProcessingResult
from squid.media.domain.models import (
    MediaArtifact,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaProbe,
    MediaProcessingAction,
    MediaProcessingReport,
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


@dataclass(frozen=True, slots=True)
class MediaProcessLimits:
    """Timeouts, thread counts, and requested OS resource limits for each media process.

    Resource limits are best effort on POSIX and unavailable on Windows. Wall-clock
    timeouts apply on both platforms.
    """

    probe_timeout_seconds: float = 15.0
    poster_timeout_seconds: float = 120.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    cpu_seconds: int = 540
    max_open_files: int = 128
    threads: int = 2

    def __post_init__(self) -> None:
        if (
            min(
                self.probe_timeout_seconds,
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


class FfmpegMediaProcessor:
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

    async def process(
        self,
        request: MediaProcessingRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaProcessingResult:
        """Copy originals and generate video thumbnails, publishing after validation."""
        if request.kind is MediaKind.IMAGE:
            if probe.original_image_content_type is None:
                raise InvalidMediaError(MediaFailureReason.FORMAT_UNSUPPORTED)
            return await self._preserve_image(request, probe=probe, source_bytes=source_bytes, limits=limits)
        return await self._preserve_video(request, probe=probe, source_bytes=source_bytes, limits=limits)

    async def aclose(self) -> None:
        """One-shot FFmpeg processes leave no reusable resources."""

    async def discard(self, result: MediaProcessingResult) -> None:
        """Remove job-local outputs after an application postflight failure."""
        try:
            result.output_path.unlink(missing_ok=True)
            if result.poster_path is not None:
                result.poster_path.unlink(missing_ok=True)
        except OSError as exc:
            raise MediaProcessingError(MediaFailureReason.OUTPUT_INVALID, operation="discard_output") from exc

    async def _preserve_image(
        self,
        request: MediaProcessingRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaProcessingResult:
        assert probe.original_image_content_type is not None
        with _staged_outputs((request.output_path, ".image")) as (temporary,):
            with CancelScope(shield=True):
                await asyncio.to_thread(shutil.copyfile, request.source_path, temporary)
            size = _bounded_size(temporary, limits.max_output_bytes)
            artifact = _artifact(temporary, probe.original_image_content_type, probe, byte_size=size)
        return MediaProcessingResult(
            output_path=request.output_path,
            poster_path=None,
            report=MediaProcessingReport(
                kind=MediaKind.IMAGE,
                source_bytes=source_bytes,
                input_probe=probe,
                output_probe=probe,
                output=artifact,
                poster=None,
                actions=(),
            ),
        )

    async def _preserve_video(
        self,
        request: MediaProcessingRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaProcessingResult:
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
            raise InvalidMediaError(MediaFailureReason.FORMAT_UNSUPPORTED)
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
        actions = [MediaProcessingAction.POSTER_GENERATED]
        if probe.has_audio:
            actions.append(MediaProcessingAction.AUDIO_PRESERVED)
        return MediaProcessingResult(
            output_path=request.output_path,
            poster_path=request.poster_path,
            report=MediaProcessingReport(
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
        color_parameters = await self._thumbnail_color_parameters(source_path, probe)
        seek_milliseconds = min((probe.duration_milliseconds or 0) // 2, 30_000)
        await self._run_ffmpeg(
            self._input(source_path, seek=_format_milliseconds(seek_milliseconds))
            .video_stream(0)
            .setparams(
                colorspace=color_parameters.get("colorspace", -1),
                color_primaries=color_parameters.get("color_primaries", -1),
                color_trc=color_parameters.get("color_trc", -1),
            )
            .scale(w="min(1920,iw)", h=-2)
            .output(
                filename=poster_temporary,
                vframes=1,
                an=True,
                sn=True,
                dn=True,
                map_metadata=-1,
                vcodec="mjpeg",
                q=2,
                pix_fmt="yuvj420p",
                codec_options=encoder_codec_context(threads=self._process_limits.threads, flags="+bitexact"),
                format_options=encoder_format_context(fflags="+bitexact"),
                f="image2",
                extra_options={"update": 1},
            ),
            operation="generate_poster",
            timeout_seconds=self._process_limits.poster_timeout_seconds,
            max_file_bytes=limits.max_output_bytes,
        )

    async def _thumbnail_color_parameters(self, source_path: Path, probe: MediaProbe) -> dict[str, int]:
        if probe.video_codec != "h264":
            return {}
        data = _ColorProbeJson.model_validate_json(
            await self._probe_output(source_path, "stream=codec_type,color_space,color_primaries,color_transfer")
        )
        stream = next(stream for stream in data.streams if stream.codec_type == "video")
        # Reserved color tags can break thumbnail scaling. Mark only those decoded
        # frame properties unspecified; the original bitstream stays untouched.
        return {
            option: 2
            for field, option in (
                ("color_space", "colorspace"),
                ("color_primaries", "color_primaries"),
                ("color_transfer", "color_trc"),
            )
            if getattr(stream, field) == "reserved"
        }

    def _input(self, source_path: Path, *, seek: str | None = None) -> ffmpeg.AVStream:
        return ffmpeg.input(
            source_path.absolute(),
            ss=seek,
            codec_options=decoder_codec_context(threads=self._process_limits.threads),
            extra_options={
                "protocol_whitelist": _INPUT_PROTOCOLS,
                "protocol_blacklist": _NETWORK_PROTOCOLS,
            },
        )

    async def _run_ffmpeg(
        self,
        output: OutputStream,
        *,
        operation: str,
        timeout_seconds: float,
        max_file_bytes: int,
    ) -> None:
        # Compile only: our runner owns cancellation, process limits, and error translation.
        command = output.global_args(
            y=True,
            stdin=False,
            hide_banner=True,
            loglevel="error",
            xerror=True,
            filter_threads=self._process_limits.threads,
            filter_complex_threads=self._process_limits.threads,
        ).compile(cmd=self._ffmpeg)
        await self._run(
            command[0],
            command[1:],
            operation=operation,
            timeout_seconds=timeout_seconds,
            max_file_bytes=max_file_bytes,
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
        descriptor, name = tempfile.mkstemp(prefix=".processing-", suffix=suffix, dir=destination.parent)
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
