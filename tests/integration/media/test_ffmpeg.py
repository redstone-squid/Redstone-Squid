import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from squid.media.application import MediaNormalizationRequest, MediaNormalizationService
from squid.media.domain import MediaKind, MediaNormalizationAction
from squid.media.errors import InvalidMediaError, MediaFailureReason
from squid.media.infrastructure import FfmpegMediaNormalizer

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="requires ffmpeg and ffprobe")


async def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.gif"
    source.write_bytes(
        bytes.fromhex("47494638396101000100800000000000ffffff21f90400000000002c00000000010001000002024401003b")
    )
    output = tmp_path / "normalized.png"
    output.write_bytes(b"keep-me")
    service = MediaNormalizationService(FfmpegMediaNormalizer())

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(MediaNormalizationRequest(kind=MediaKind.IMAGE, source_path=source, output_path=output))

    assert raised.value.reason is MediaFailureReason.OUTPUT_EXISTS
    assert output.read_bytes() == b"keep-me"


async def test_video_preserves_original_bytes_and_audio(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    _generate_video(source)
    output = tmp_path / "normalized.mp4"
    poster = tmp_path / "poster.jpg"
    service = MediaNormalizationService(FfmpegMediaNormalizer())

    result = await service.normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=output,
            poster_path=poster,
        )
    )

    report = result.report
    assert report.output_probe.video_codec == "h264"
    assert report.output_probe.audio_codec == "aac"
    assert (report.output_probe.width, report.output_probe.height) == (320, 180)
    assert (report.output_probe.frame_rate_numerator, report.output_probe.frame_rate_denominator) == (24, 1)
    assert report.poster is not None
    assert poster.read_bytes().startswith(b"\xff\xd8")
    assert MediaNormalizationAction.AUDIO_PRESERVED in report.actions

    assert FFPROBE is not None
    metadata = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format_tags:stream_tags",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert b"private-marker" in metadata
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    ("matrix", "primaries", "transfer"),
    [(3, 0, 6), (1, 1, 1), (1, 0, 1), (1, 1, 0)],
)
async def test_video_repairs_reserved_color_tags_and_preserves_valid_tags(
    tmp_path: Path, matrix: int, primaries: int, transfer: int
) -> None:
    assert FFMPEG is not None
    assert FFPROBE is not None
    original = tmp_path / "original.mp4"
    source = tmp_path / "tagged.mp4"
    _generate_video(original)
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-i",
            str(original),
            "-map",
            "0",
            "-c",
            "copy",
            "-bsf:v",
            f"h264_metadata=matrix_coefficients={matrix}:colour_primaries={primaries}"
            f":transfer_characteristics={transfer}",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    result = await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "normalized.mp4",
            poster_path=tmp_path / "poster.jpg",
        )
    )

    probe = result.report.output_probe
    assert (probe.width, probe.height) == (320, 180)
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (24, 1)
    assert probe.frame_count == result.report.input_probe.frame_count == 24
    assert probe.duration_milliseconds is not None
    assert result.report.input_probe.duration_milliseconds is not None
    assert abs(probe.duration_milliseconds - result.report.input_probe.duration_milliseconds) <= 50
    assert probe.has_audio
    assert result.report.poster is not None
    metadata = json.loads(
        subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(result.output_path)],
            check=True,
            capture_output=True,
        ).stdout
    )["streams"][0]
    for field, value in (("color_space", matrix), ("color_primaries", primaries), ("color_transfer", transfer)):
        if value == 1:
            assert metadata[field] == "bt709"
        elif value == 6:
            assert metadata[field] == "smpte170m"
        else:
            assert metadata.get(field, "unknown") in {"unknown", "reserved"}


async def test_video_color_repair_ignores_malformed_filler_packets(tmp_path: Path) -> None:
    assert FFMPEG is not None
    original = tmp_path / "original.mp4"
    raw = tmp_path / "stream.h264"
    source = tmp_path / "padding.mp4"
    _generate_video(original, b_frames=0)
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb,h264_metadata=matrix_coefficients=3:colour_primaries=0",
            "-f",
            "h264",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    data = raw.read_bytes()
    first_picture = data.index(b"\x00\x00\x01\x65")
    # Some recorders omit the filler packet's trailing stop bit. It carries no picture data.
    raw.write_bytes(data[:first_picture] + b"\x00\x00\x01\x0c" + data[first_picture:])
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-r",
            "24",
            "-i",
            str(raw),
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    result = await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "out.mp4",
            poster_path=tmp_path / "poster.jpg",
        )
    )

    assert (result.report.output_probe.width, result.report.output_probe.height) == (320, 180)
    assert result.report.output_probe.frame_count == result.report.input_probe.frame_count == 24
    assert result.report.output_probe.has_audio
    assert result.report.poster is not None


async def test_4k_video_fits_the_default_process_memory_limit(tmp_path: Path) -> None:
    assert FFMPEG is not None
    source = tmp_path / "4k.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=3840x2160:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    service = MediaNormalizationService(FfmpegMediaNormalizer())

    result = await service.normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "normalized.mp4",
            poster_path=tmp_path / "poster.jpg",
        )
    )

    probe = result.report.output_probe
    assert (probe.width, probe.height) == (3840, 2160)
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (24, 1)
    assert probe.frame_count == 72
    assert probe.duration_milliseconds == result.report.input_probe.duration_milliseconds
    assert probe.has_audio
    assert result.report.poster is not None


async def test_variable_rate_video_preserves_closely_spaced_frame_timestamps(tmp_path: Path) -> None:
    source = tmp_path / "variable.mp4"
    _generate_video(source, variable_timestamps=True)
    service = MediaNormalizationService(FfmpegMediaNormalizer())
    result = await service.normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "normalized.mp4",
            poster_path=tmp_path / "poster.jpg",
        )
    )
    assert FFPROBE is not None
    timestamps: list[list[Decimal]] = []
    for path in (source, result.output_path):
        frames = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        timestamps.append(
            [Decimal(frame["best_effort_timestamp_time"]) for frame in json.loads(frames.stdout)["frames"]]
        )

    original, normalized = timestamps
    assert len(original) == len(normalized) == 24
    assert 0 < original[1] - original[0] < Decimal("0.001")
    for index in range(1, len(original)):
        original_interval = original[index] - original[index - 1]
        normalized_interval = normalized[index] - normalized[index - 1]
        assert abs(original_interval - normalized_interval) <= Decimal("0.000001")


@pytest.mark.parametrize("rotation", [-90, 90, 180])
async def test_rotated_video_preserves_display_orientation(tmp_path: Path, rotation: int) -> None:
    original = tmp_path / "original.mp4"
    source = tmp_path / "rotated.mov"
    _generate_video(original)
    assert FFMPEG is not None
    assert FFPROBE is not None
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-display_rotation:v:0",
            str(rotation),
            "-i",
            str(original),
            "-map",
            "0",
            "-c",
            "copy",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "normalized.mp4",
            poster_path=tmp_path / "poster.jpg",
        )
    )

    expected = (180, 320) if abs(rotation) == 90 else (320, 180)
    assert (result.report.input_probe.width, result.report.input_probe.height) == expected
    raw_output = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:stream_side_data=rotation",
            "-of",
            "json",
            str(result.output_path),
        ],
        check=True,
        capture_output=True,
    )
    stream = json.loads(raw_output.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (320, 180)
    assert any(side.get("rotation", 0) % 360 == rotation % 360 for side in stream.get("side_data_list", []))
    assert result.output_path.read_bytes() == source.read_bytes()
    frames = [
        subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            check=True,
            capture_output=True,
        ).stdout
        for path in (source, result.output_path)
    ]
    assert len(frames[0]) == len(frames[1]) == expected[0] * expected[1] * 3
    assert sum(abs(a - b) for a, b in zip(*frames, strict=True)) / len(frames[0]) < 6


def _generate_video(destination: Path, *, variable_timestamps: bool = False, b_frames: int | None = None) -> None:
    assert FFMPEG is not None
    encoders = subprocess.run(
        [FFMPEG, "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
    )
    if "libx264" not in encoders.stdout:
        pytest.skip("ffmpeg lacks the libx264 encoder")
    timestamp_arguments = (
        [
            "-vf",
            r"settb=expr=1/60000,setpts=if(eq(N\,1)\,1\,N*2500)",
            "-fps_mode",
            "passthrough",
            "-enc_time_base:v",
            "1:60000",
        ]
        if variable_timestamps
        else []
    )
    process = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-shortest",
            "-metadata",
            "comment=private-marker",
            *timestamp_arguments,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            *([] if b_frames is None else ["-bf", str(b_frames)]),
            "-c:a",
            "aac",
            str(destination),
        ],
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        pytest.skip("local ffmpeg could not generate the integration fixture")


async def test_webm_is_preserved_with_odd_dimensions_and_unknown_duration(tmp_path: Path) -> None:
    source = tmp_path / "source.webm"
    subprocess.run(
        [
            FFMPEG or "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=321x181:rate=12",
            "-t",
            "1",
            "-c:v",
            "libvpx-vp9",
            "-live",
            "1",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
        MediaNormalizationRequest(
            kind=MediaKind.VIDEO,
            source_path=source,
            output_path=tmp_path / "output",
            poster_path=tmp_path / "poster.jpg",
        )
    )
    assert result.output_path.read_bytes() == source.read_bytes()
    assert result.report.output.content_type == "video/webm"
    assert result.report.input_probe.duration_milliseconds is None
    assert result.report.output_probe == result.report.input_probe
    assert result.poster_path is not None
    assert result.poster_path.read_bytes().startswith(b"\xff\xd8")


@pytest.mark.parametrize("extension", ["png", "gif", "jpg", "webp"])
async def test_raster_originals_preserve_bytes_and_animation(tmp_path: Path, extension: str) -> None:
    source = tmp_path / f"source.{extension}"
    subprocess.run(
        [
            FFMPEG or "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=32x24:rate=4",
            "-frames:v",
            "4" if extension == "gif" else "1",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
        MediaNormalizationRequest(kind=MediaKind.IMAGE, source_path=source, output_path=tmp_path / "output")
    )
    assert result.output_path.read_bytes() == source.read_bytes()
    assert result.report.actions == ()
    assert result.report.output.content_type == {"jpg": "image/jpeg"}.get(extension, f"image/{extension}")


async def test_unsupported_images_are_rejected_without_conversion(tmp_path: Path) -> None:
    source = tmp_path / "source.ppm"
    source.write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")
    output = tmp_path / "output"
    with pytest.raises(InvalidMediaError) as raised:
        await MediaNormalizationService(FfmpegMediaNormalizer()).normalize(
            MediaNormalizationRequest(kind=MediaKind.IMAGE, source_path=source, output_path=output)
        )
    assert raised.value.reason is MediaFailureReason.FORMAT_UNSUPPORTED
    assert not output.exists()
