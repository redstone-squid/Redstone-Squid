import json
from pathlib import Path

import pytest

from squid.media.errors import InvalidMediaError, MediaFailureReason, MediaToolUnavailableError
from squid.media.infrastructure import FfmpegMediaNormalizer, MediaProcessLimits, parse_ffprobe_output


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
