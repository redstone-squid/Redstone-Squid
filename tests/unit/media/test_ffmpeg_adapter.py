"""Media subprocess cleanup and probe parsing, using Python children without requiring FFmpeg."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from anyio import CancelScope, create_task_group, fail_after, sleep

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.domain.models import MediaKind, MediaLimits, MediaProbe
from squid.media.errors import (
    InvalidMediaError,
    MediaFailureReason,
    MediaProcessingError,
    MediaProcessingTimeoutError,
    MediaToolUnavailableError,
)
from squid.media.infrastructure import FfmpegMediaNormalizer, MediaProcessLimits, parse_ffprobe_output
from squid.media.infrastructure.ffmpeg import _staged_outputs, _validate_video_output


@pytest.fixture
async def child_processes(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[asyncio.subprocess.Process]]:
    """Record real children and reap them even when a lifecycle assertion fails."""
    children: list[asyncio.subprocess.Process] = []
    spawn = asyncio.create_subprocess_exec

    async def record_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await spawn(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    yield children
    with CancelScope(shield=True):
        for process in children:
            if process.returncode is None:
                process.kill()
            await process.communicate()


async def run_python_tool(script: str, *, timeout_seconds: float = 5) -> bytes:
    return await FfmpegMediaNormalizer()._run(
        sys.executable,
        ("-c", script),
        operation="test_tool",
        timeout_seconds=timeout_seconds,
        max_file_bytes=1024 * 1024,
        capture_stdout=True,
    )


async def test_tool_timeout_reaps_child_before_reporting_failure(
    child_processes: list[asyncio.subprocess.Process],
) -> None:
    with pytest.raises(MediaProcessingTimeoutError) as raised:
        await run_python_tool("import time; time.sleep(60)", timeout_seconds=0.05)

    assert raised.value.context["operation"] == "test_tool"
    (child,) = child_processes
    assert child.returncode is not None


async def test_task_group_cancellation_reaps_child_and_drains_its_pipes(
    tmp_path: Path, child_processes: list[asyncio.subprocess.Process]
) -> None:
    # Write continuously to both pipes so cancellation also exercises pipe cleanup.
    ready = tmp_path / "ready"
    script = f"""
import os
from pathlib import Path
Path({str(ready)!r}).touch()
while True:
    os.write(1, b'o' * 65536)
    os.write(2, b'e' * 65536)
"""
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(run_python_tool, script)
            while not ready.exists():
                await sleep(0.001)
            tasks.cancel_scope.cancel()

    (child,) = child_processes
    assert child.returncode is not None
    assert child.stdout is not None
    assert child.stdout.at_eof()
    assert child.stderr is not None
    assert child.stderr.at_eof()


def test_ffprobe_json_is_normalized_without_float_rounding() -> None:
    data = json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                    "r_frame_rate": "30000/1001",
                    "duration": "1.0001",
                    "nb_frames": "30",
                },
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.0"},
        }
    ).encode()

    probe = parse_ffprobe_output(data)

    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (30_000, 1_001)
    assert probe.duration_milliseconds == 1_001
    assert probe.frame_count == 30
    assert probe.audio_codec == "aac"
    assert probe.container_names[:2] == ("mov", "mp4")


def test_probe_falls_back_to_declared_rate_and_container_duration() -> None:
    data = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "png",
                    "width": 4,
                    "height": 3,
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "25/1",
                    "duration": "N/A",
                }
            ],
            "format": {"format_name": "png_pipe", "duration": "0.04"},
        }
    ).encode()

    probe = parse_ffprobe_output(data)

    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (25, 1)
    assert probe.duration_milliseconds == 40


@pytest.mark.parametrize("rotation", [0, 90, -90, 180, 270, -270, 360])
def test_probe_dimensions_follow_display_rotation(rotation: int) -> None:
    data = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 480,
                    "height": 640,
                    "side_data_list": [{"side_data_type": "Display Matrix", "rotation": rotation}],
                }
            ],
        }
    ).encode()

    probe = parse_ffprobe_output(data)

    expected = (640, 480) if abs(rotation) % 180 == 90 else (480, 640)
    assert (probe.width, probe.height) == expected


@pytest.mark.parametrize("rotation", ["NaN", "Infinity", "not-an-angle"])
def test_invalid_display_rotation_is_rejected(rotation: str) -> None:
    data = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 480,
                    "height": 640,
                    "side_data_list": [{"rotation": rotation}],
                }
            ]
        }
    ).encode()

    with pytest.raises(InvalidMediaError):
        parse_ffprobe_output(data)


@pytest.mark.parametrize("data", [b"not-json", b"{}", b'{"streams": []}'])
def test_malformed_probe_output_has_one_stable_reason(data: bytes) -> None:
    with pytest.raises(InvalidMediaError) as raised:
        parse_ffprobe_output(data)

    assert raised.value.reason is MediaFailureReason.PROBE_INVALID
    assert raised.value.public_context == {"reason": "probe_invalid"}


def test_process_limits_reject_non_positive_guardrails() -> None:
    with pytest.raises(ValueError, match="process limit"):
        MediaProcessLimits(threads=0)


async def test_missing_ffprobe_has_a_stable_non_content_error() -> None:
    normalizer = FfmpegMediaNormalizer(ffprobe="redstone-squid-missing-ffprobe")

    with pytest.raises(MediaToolUnavailableError) as raised:
        await normalizer.probe(Path("private-file-name.mp4"))

    assert raised.value.context == {
        "reason": "tool_unavailable",
        "tool": "redstone-squid-missing-ffprobe",
    }
    assert "private-file-name" not in str(raised.value)


@pytest.mark.parametrize("kind", [MediaKind.IMAGE, MediaKind.VIDEO])
async def test_cancelled_normalization_removes_every_staged_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: MediaKind
) -> None:
    normalizer = FfmpegMediaNormalizer()
    monkeypatch.setattr(normalizer, "_run", AsyncMock(side_effect=asyncio.CancelledError))
    request = MediaNormalizationRequest(
        kind=kind,
        source_path=tmp_path / "source",
        output_path=tmp_path / "output",
        poster_path=tmp_path / "poster" if kind is MediaKind.VIDEO else None,
    )
    probe = MediaProbe(
        container_names=("mp4",) if kind is MediaKind.VIDEO else ("png_pipe",),
        video_codec="png",
        width=1,
        height=1,
        frame_rate_numerator=1,
        frame_rate_denominator=1,
        duration_milliseconds=1000,
    )

    with pytest.raises(asyncio.CancelledError):
        await normalizer.normalize(request, probe=probe, source_bytes=1, limits=MediaLimits())

    assert list(tmp_path.iterdir()) == []


def test_failed_output_publication_rolls_back_only_owned_files(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    poster = tmp_path / "poster.jpg"

    def publish() -> None:
        with _staged_outputs((output, ".mp4"), (poster, ".jpg")) as staged:
            for temporary in staged:
                temporary.write_bytes(b"normalized")
            # Another writer can claim the destination during normalization.
            poster.write_bytes(b"other writer")

    with pytest.raises(InvalidMediaError):
        publish()

    assert not output.exists()
    assert poster.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [poster]


def test_failed_output_preparation_removes_earlier_staged_files(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    poster = tmp_path / "poster.jpg"
    poster.write_bytes(b"existing")

    with pytest.raises(InvalidMediaError), _staged_outputs((output, ".mp4"), (poster, ".jpg")):
        pytest.fail("An occupied destination must prevent normalization")

    assert list(tmp_path.iterdir()) == [poster]
    assert poster.read_bytes() == b"existing"


def test_video_validation_accepts_muxer_rounding_of_average_frame_rate() -> None:
    source = MediaProbe(
        container_names=("mp4",),
        video_codec="h264",
        width=854,
        height=480,
        frame_rate_numerator=31_200,
        frame_rate_denominator=1_039,
        duration_milliseconds=17_317,
        audio_codec="aac",
        frame_count=520,
    )
    output = replace(source, frame_rate_numerator=30, frame_rate_denominator=1, duration_milliseconds=17_334)

    _validate_video_output(source, output, strip_audio=False)


@pytest.mark.parametrize("output_rate", [0, 24, 60])
def test_video_validation_still_rejects_changed_or_missing_frame_rates(output_rate: int) -> None:
    source = MediaProbe(
        container_names=("mp4",),
        video_codec="h264",
        width=854,
        height=480,
        frame_rate_numerator=30,
        frame_rate_denominator=1,
        duration_milliseconds=17_334,
        frame_count=520,
    )

    with pytest.raises(MediaProcessingError):
        _validate_video_output(source, replace(source, frame_rate_numerator=output_rate), strip_audio=False)


@pytest.mark.parametrize("output_frames", [215, 214])
async def test_video_header_mismatch_is_checked_against_decoded_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_frames: int
) -> None:
    source = MediaProbe(
        container_names=("mp4",),
        video_codec="h264",
        width=1724,
        height=1080,
        frame_rate_numerator=60,
        frame_rate_denominator=1,
        duration_milliseconds=3600,
        audio_codec="aac",
        frame_count=216,
    )
    output = replace(source, duration_milliseconds=3584, frame_count=215)
    poster = replace(output, video_codec="mjpeg", audio_codec=None, container_names=("jpeg_pipe",))
    normalizer = FfmpegMediaNormalizer()

    async def write_output(executable: str, arguments: Sequence[str], **kwargs: Any) -> bytes:
        Path(arguments[-1]).write_bytes(b"normalized")
        return b""

    monkeypatch.setattr(normalizer, "_run", write_output)
    monkeypatch.setattr(normalizer, "_video_color_arguments", AsyncMock(return_value=()))
    monkeypatch.setattr(normalizer, "probe", AsyncMock(side_effect=[output, poster]))
    count_frames = AsyncMock(side_effect=[215, output_frames])
    monkeypatch.setattr(normalizer, "_decoded_frame_count", count_frames)
    request = MediaNormalizationRequest(
        kind=MediaKind.VIDEO,
        source_path=tmp_path / "source",
        output_path=tmp_path / "output.mp4",
        poster_path=tmp_path / "poster.jpg",
    )

    if output_frames == 215:
        result = await normalizer._normalize_video(request, probe=source, source_bytes=1, limits=MediaLimits())
        assert result.output_path.exists()
        assert result.report.input_probe.frame_count == 216
        assert result.report.output_probe.frame_count == 215
    else:
        with pytest.raises(MediaProcessingError):
            await normalizer._normalize_video(request, probe=source, source_bytes=1, limits=MediaLimits())
        assert not request.output_path.exists()
    assert count_frames.await_count == 2


@pytest.mark.parametrize("count", [None, "N/A", "0", "-1"])
async def test_unavailable_decoded_counts_cannot_prove_frame_preservation(
    monkeypatch: pytest.MonkeyPatch, count: str | None
) -> None:
    normalizer = FfmpegMediaNormalizer()
    monkeypatch.setattr(
        normalizer, "_run", AsyncMock(return_value=json.dumps({"streams": [{"nb_read_frames": count}]}).encode())
    )

    with pytest.raises(MediaProcessingError) as raised:
        await normalizer._decoded_frame_count(Path("source.mp4"))

    assert raised.value.context["operation"] == "count_frames"


@pytest.mark.parametrize("change", ["offset", "cadence", "missing_frame"])
async def test_rate_metadata_fallback_requires_matching_frame_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    source = MediaProbe(
        container_names=("mp4",),
        video_codec="h264",
        width=1200,
        height=700,
        frame_rate_numerator=4_830_000,
        frame_rate_denominator=162_821,
        duration_milliseconds=10_855,
        frame_count=322,
    )
    output = replace(source, frame_rate_numerator=100_625, frame_rate_denominator=3413, duration_milliseconds=10_872)
    poster = replace(output, video_codec="mjpeg", container_names=("jpeg_pipe",))
    source_timestamps = tuple(Decimal(index) / 30 for index in range(322))
    output_timestamps = tuple(value + Decimal("0.05") for value in source_timestamps)
    if change == "cadence":
        output_timestamps = (
            *output_timestamps[:100],
            output_timestamps[100] + Decimal("0.01"),
            *output_timestamps[101:],
        )
    elif change == "missing_frame":
        output_timestamps = output_timestamps[:-1]
    normalizer = FfmpegMediaNormalizer()

    async def write_output(executable: str, arguments: Sequence[str], **kwargs: Any) -> bytes:
        Path(arguments[-1]).write_bytes(b"normalized")
        return b""

    monkeypatch.setattr(normalizer, "_run", write_output)
    monkeypatch.setattr(normalizer, "_video_color_arguments", AsyncMock(return_value=()))
    monkeypatch.setattr(normalizer, "probe", AsyncMock(side_effect=[output, poster]))
    timestamps = AsyncMock(side_effect=[source_timestamps, output_timestamps])
    monkeypatch.setattr(normalizer, "_frame_timestamps", timestamps)
    request = MediaNormalizationRequest(
        kind=MediaKind.VIDEO,
        source_path=tmp_path / "source",
        output_path=tmp_path / "output.mp4",
        poster_path=tmp_path / "poster.jpg",
    )

    if change == "offset":
        result = await normalizer._normalize_video(request, probe=source, source_bytes=1, limits=MediaLimits())
        assert result.output_path.exists()
        assert result.report.input_probe == source
        assert result.report.output_probe == output
    else:
        with pytest.raises(MediaProcessingError):
            await normalizer._normalize_video(request, probe=source, source_bytes=1, limits=MediaLimits())
        assert not request.output_path.exists()
    assert timestamps.await_count == 2


@pytest.mark.parametrize("regression", ["0.000065", "0.001"])
async def test_submillisecond_decoder_estimates_are_preserved(monkeypatch: pytest.MonkeyPatch, regression: str) -> None:
    normalizer = FfmpegMediaNormalizer()
    values = (Decimal("6.4"), Decimal("6.400065"), Decimal("6.400065") - Decimal(regression))
    payload = {"frames": [{"best_effort_timestamp_time": str(value)} for value in values]}
    monkeypatch.setattr(normalizer, "_run", AsyncMock(return_value=json.dumps(payload).encode()))

    assert await normalizer._frame_timestamps(Path("source.mp4")) == values


@pytest.mark.parametrize("values", [[], ["N/A"], ["NaN"], ["Infinity"], ["0.2", "0.1"], ["6.4", "6.398999"]])
async def test_invalid_frame_timestamps_cannot_prove_timing_preservation(
    monkeypatch: pytest.MonkeyPatch, values: list[str]
) -> None:
    normalizer = FfmpegMediaNormalizer()
    payload = {"frames": [{"best_effort_timestamp_time": value} for value in values]}
    monkeypatch.setattr(normalizer, "_run", AsyncMock(return_value=json.dumps(payload).encode()))

    with pytest.raises(MediaProcessingError) as raised:
        await normalizer._frame_timestamps(Path("source.mp4"))

    assert raised.value.context["operation"] == "frame_timestamps"
