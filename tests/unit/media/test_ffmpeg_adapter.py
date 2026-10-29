import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from squid.media.application.commands import MediaNormalizationRequest
from squid.media.domain.models import MediaKind, MediaLimits, MediaProbe
from squid.media.errors import InvalidMediaError, MediaFailureReason, MediaToolUnavailableError
from squid.media.infrastructure import FfmpegMediaNormalizer, MediaProcessLimits, parse_ffprobe_output
from squid.media.infrastructure.ffmpeg import _staged_outputs


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
        container_names=("png_pipe",),
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
