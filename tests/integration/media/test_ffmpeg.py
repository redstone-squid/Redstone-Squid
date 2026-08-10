import shutil
import subprocess
from pathlib import Path

import pytest

from squid.media.application import MediaNormalizationRequest, MediaNormalizationService
from squid.media.domain import MediaKind, MediaNormalizationAction
from squid.media.errors import InvalidMediaError, MediaFailureReason
from squid.media.infrastructure import FfmpegMediaNormalizer

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="requires ffmpeg and ffprobe")


async def test_image_is_decoded_and_reencoded_as_metadata_free_png(tmp_path: Path) -> None:
    source = tmp_path / "source.ppm"
    source.write_bytes(b"P6\n# private-marker\n2 1\n255\n" + b"\xff\x00\x00\x00\xff\x00")
    output = tmp_path / "normalized.png"
    service = MediaNormalizationService(FfmpegMediaNormalizer())

    result = await service.normalize(
        MediaNormalizationRequest(kind=MediaKind.IMAGE, source_path=source, output_path=output)
    )

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result.report.output.content_type == "image/png"
    assert (result.report.output.width, result.report.output.height) == (2, 1)
    assert result.report.actions == (
        MediaNormalizationAction.METADATA_REMOVED,
        MediaNormalizationAction.IMAGE_REENCODED,
    )
    assert b"private-marker" not in output.read_bytes()


async def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.ppm"
    source.write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")
    output = tmp_path / "normalized.png"
    output.write_bytes(b"keep-me")
    service = MediaNormalizationService(FfmpegMediaNormalizer())

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(MediaNormalizationRequest(kind=MediaKind.IMAGE, source_path=source, output_path=output))

    assert raised.value.reason is MediaFailureReason.OUTPUT_EXISTS
    assert output.read_bytes() == b"keep-me"


@pytest.mark.parametrize("strip_audio", [False, True])
async def test_video_preserves_dimensions_and_fps_with_optional_audio_removal(
    tmp_path: Path,
    strip_audio: bool,
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
            strip_audio=strip_audio,
        )
    )

    report = result.report
    assert report.output_probe.video_codec == "h264"
    assert report.output_probe.audio_codec == (None if strip_audio else "aac")
    assert (report.output_probe.width, report.output_probe.height) == (320, 180)
    assert (report.output_probe.frame_rate_numerator, report.output_probe.frame_rate_denominator) == (24, 1)
    assert report.poster is not None
    assert poster.read_bytes().startswith(b"\xff\xd8")
    assert (MediaNormalizationAction.AUDIO_REMOVED in report.actions) is strip_audio
    assert (MediaNormalizationAction.AUDIO_PRESERVED in report.actions) is not strip_audio

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
    assert b"private-marker" not in metadata


def _generate_video(destination: Path) -> None:
    assert FFMPEG is not None
    encoders = subprocess.run(
        [FFMPEG, "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
    )
    if "libx264" not in encoders.stdout:
        pytest.skip("ffmpeg lacks the libx264 encoder")
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
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(destination),
        ],
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        pytest.skip("local ffmpeg could not generate the integration fixture")
