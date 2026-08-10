import pytest

from squid.media.domain import (
    MediaArtifact,
    MediaBatchTotals,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaProbe,
)


def probe(
    *,
    width: int = 1920,
    height: int = 1080,
    fps_numerator: int = 60,
    fps_denominator: int = 1,
    duration_milliseconds: int | None = 1_000,
) -> MediaProbe:
    return MediaProbe(
        container_names=("mp4",),
        video_codec="h264",
        width=width,
        height=height,
        frame_rate_numerator=fps_numerator,
        frame_rate_denominator=fps_denominator,
        duration_milliseconds=duration_milliseconds,
    )


def test_default_limits_match_the_advertised_submission_policy() -> None:
    limits = MediaLimits()

    assert limits.max_images == 10
    assert limits.max_videos == 3
    assert limits.max_duration_milliseconds == 300_000
    assert limits.max_source_bytes == 500 * 1024 * 1024
    assert limits.max_output_bytes == 500 * 1024 * 1024
    assert limits.max_pixels_per_frame == 33_200_000
    assert limits.max_decoded_pixels_per_second == 250_000_000


def test_batch_violations_use_a_stable_priority() -> None:
    limits = MediaLimits()
    totals = MediaBatchTotals(image_count=11, video_count=4, source_bytes=limits.max_source_bytes + 1)

    violation = limits.batch_violation(totals)

    assert violation is not None
    assert violation.measure is MediaLimitMeasure.IMAGE_COUNT
    assert (violation.actual, violation.limit) == (11, 10)


def test_decoded_pixel_rate_uses_exact_rational_arithmetic_and_rounds_up() -> None:
    inspected = probe(width=101, height=99, fps_numerator=30_000, fps_denominator=1_001)

    expected_numerator = 101 * 99 * 30_000
    expected = (expected_numerator + 1_000) // 1_001

    assert inspected.decoded_pixels_per_second == expected


@pytest.mark.parametrize(
    ("inspected", "expected_measure"),
    [
        (probe(width=8_000, height=8_000), MediaLimitMeasure.PIXELS_PER_FRAME),
        (probe(duration_milliseconds=300_001), MediaLimitMeasure.DURATION_MILLISECONDS),
        (probe(width=2_500, height=2_000, fps_numerator=51), MediaLimitMeasure.DECODED_PIXELS_PER_SECOND),
    ],
)
def test_video_probe_limits_are_reported_by_stable_measure(
    inspected: MediaProbe,
    expected_measure: MediaLimitMeasure,
) -> None:
    violation = MediaLimits().probe_violation(MediaKind.VIDEO, inspected)

    assert violation is not None
    assert violation.measure is expected_measure


def test_artifact_digest_must_be_canonical_sha256() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        MediaArtifact(content_type="image/png", byte_size=1, sha256="ABC", width=1, height=1)
