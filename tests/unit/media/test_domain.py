import pytest

from squid.core.media.domain import (
    MediaArtifact,
    MediaBatchTotals,
    MediaLimitMeasure,
    MediaLimits,
)
from squid.core.media.errors import MediaLimitExceededError


def test_default_limits_match_the_advertised_submission_policy() -> None:
    limits = MediaLimits()

    assert limits.max_images == 10
    assert limits.max_videos == 3

    assert limits.max_source_bytes == 500 * 1024 * 1024
    assert limits.max_output_bytes == 500 * 1024 * 1024


def test_batch_violations_use_a_stable_priority() -> None:
    limits = MediaLimits()
    totals = MediaBatchTotals(image_count=11, video_count=4, source_bytes=limits.max_source_bytes + 1)

    violations = limits.batch_violations(totals)

    assert [violation.measure for violation in violations] == [
        MediaLimitMeasure.IMAGE_COUNT,
        MediaLimitMeasure.VIDEO_COUNT,
        MediaLimitMeasure.SOURCE_BYTES,
    ]
    assert [(violation.actual, violation.limit) for violation in violations] == [
        (11, 10),
        (4, 3),
        (limits.max_source_bytes + 1, limits.max_source_bytes),
    ]


def test_limit_error_exposes_every_measure_and_limit_without_attacker_derived_actuals() -> None:
    limits = MediaLimits(max_images=1, max_videos=1)
    violations = limits.batch_violations(MediaBatchTotals(image_count=3, video_count=2))

    error = MediaLimitExceededError(violations)

    assert error.public_context == {
        "reason": "limit_exceeded",
        "violations": [
            {"measure": "image_count", "limit": 1},
            {"measure": "video_count", "limit": 1},
        ],
    }
    assert error.context == {
        "reason": "limit_exceeded",
        "violations": [
            {"measure": "image_count", "actual": 3, "limit": 1},
            {"measure": "video_count", "actual": 2, "limit": 1},
        ],
    }
    assert "actual" not in str(error.public_context)


def test_artifact_digest_must_be_canonical_sha256() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        MediaArtifact(content_type="image/png", byte_size=1, sha256="ABC", width=1, height=1)
