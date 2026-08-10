from pathlib import Path

import pytest

from squid.media.application import (
    MediaNormalizationRequest,
    MediaNormalizationResult,
    MediaNormalizationService,
)
from squid.media.domain import (
    MediaArtifact,
    MediaBatchTotals,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaNormalizationAction,
    MediaNormalizationReport,
    MediaProbe,
)
from squid.media.errors import InvalidMediaError, MediaFailureReason, MediaLimitExceededError


def video_probe(
    *,
    width: int = 320,
    height: int = 180,
    fps_numerator: int = 24,
    duration_milliseconds: int | None = 1_000,
    audio_codec: str | None = "aac",
) -> MediaProbe:
    return MediaProbe(
        container_names=("mov", "mp4"),
        video_codec="h264",
        width=width,
        height=height,
        frame_rate_numerator=fps_numerator,
        frame_rate_denominator=1,
        duration_milliseconds=duration_milliseconds,
        audio_codec=audio_codec,
        frame_count=24,
    )


def artifact(
    *, content_type: str = "video/mp4", byte_size: int = 100, width: int = 320, height: int = 180
) -> MediaArtifact:
    return MediaArtifact(
        content_type=content_type,
        byte_size=byte_size,
        sha256="a" * 64,
        width=width,
        height=height,
    )


class FakeMediaNormalizer:
    def __init__(self, inspected: MediaProbe) -> None:
        self.inspected = inspected
        self.probe_calls: list[Path] = []
        self.normalize_calls: list[tuple[MediaNormalizationRequest, int, MediaLimits]] = []
        self.discard_calls: list[MediaNormalizationResult] = []
        self.closed = False
        self.change_on_probe = False
        self.change_on_normalize = False
        self.output_bytes = 100

    async def probe(self, source_path: Path) -> MediaProbe:
        self.probe_calls.append(source_path)
        if self.change_on_probe:
            source_path.write_bytes(b"changed during probe")
        return self.inspected

    async def normalize(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        self.normalize_calls.append((request, source_bytes, limits))
        if self.change_on_normalize:
            request.source_path.write_bytes(b"changed during normalization")
        poster = artifact(content_type="image/jpeg", byte_size=10) if request.poster_path is not None else None
        report = MediaNormalizationReport(
            kind=request.kind,
            source_bytes=source_bytes,
            input_probe=probe,
            output_probe=probe,
            output=artifact(byte_size=self.output_bytes),
            poster=poster,
            actions=(MediaNormalizationAction.METADATA_REMOVED,),
        )
        return MediaNormalizationResult(
            output_path=request.output_path,
            poster_path=request.poster_path,
            report=report,
        )

    async def aclose(self) -> None:
        self.closed = True

    async def discard(self, result: MediaNormalizationResult) -> None:
        self.discard_calls.append(result)


def request(tmp_path: Path) -> MediaNormalizationRequest:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return MediaNormalizationRequest(
        kind=MediaKind.VIDEO,
        source_path=source,
        output_path=tmp_path / "normalized.mp4",
        poster_path=tmp_path / "poster.jpg",
    )


async def test_service_validates_then_delegates_to_the_normalizer(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe())
    service = MediaNormalizationService(normalizer)
    command = request(tmp_path)

    result = await service.normalize(command)

    assert result.report.source_bytes == len(b"video")
    assert normalizer.probe_calls == [command.source_path]
    assert normalizer.normalize_calls == [(command, len(b"video"), service.limits)]


async def test_source_byte_budget_is_checked_before_probe(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe())
    service = MediaNormalizationService(normalizer, limits=MediaLimits(max_source_bytes=4))
    command = request(tmp_path)

    with pytest.raises(MediaLimitExceededError) as raised:
        await service.normalize(command)

    assert raised.value.violation.measure is MediaLimitMeasure.SOURCE_BYTES
    assert normalizer.probe_calls == []


@pytest.mark.parametrize(
    ("inspected", "reason"),
    [
        (video_probe(duration_milliseconds=None), MediaFailureReason.VIDEO_DURATION_UNKNOWN),
        (video_probe(fps_numerator=0), MediaFailureReason.VIDEO_FRAME_RATE_UNKNOWN),
        (video_probe(width=321), MediaFailureReason.VIDEO_DIMENSIONS_UNSUPPORTED),
    ],
)
async def test_unsafe_video_probe_facts_are_rejected_before_decode(
    tmp_path: Path,
    inspected: MediaProbe,
    reason: MediaFailureReason,
) -> None:
    normalizer = FakeMediaNormalizer(inspected)
    service = MediaNormalizationService(normalizer)

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(request(tmp_path))

    assert raised.value.reason is reason
    assert normalizer.normalize_calls == []


async def test_decoded_work_budget_is_checked_before_decode(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe(width=2_500, height=2_000, fps_numerator=51))
    service = MediaNormalizationService(normalizer)

    with pytest.raises(MediaLimitExceededError) as raised:
        await service.normalize(request(tmp_path))

    assert raised.value.violation.measure is MediaLimitMeasure.DECODED_PIXELS_PER_SECOND
    assert normalizer.normalize_calls == []


async def test_source_changes_between_probe_and_decode_are_rejected(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe())
    normalizer.change_on_probe = True
    service = MediaNormalizationService(normalizer)

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(request(tmp_path))

    assert raised.value.reason is MediaFailureReason.SOURCE_CHANGED
    assert normalizer.discard_calls == []
    assert normalizer.normalize_calls == []


async def test_source_changes_during_decode_are_rejected(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe())
    normalizer.change_on_normalize = True
    service = MediaNormalizationService(normalizer)

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(request(tmp_path))

    assert raised.value.reason is MediaFailureReason.SOURCE_CHANGED
    assert len(normalizer.discard_calls) == 1


def test_submission_wide_image_and_video_counts_are_enforced() -> None:
    service = MediaNormalizationService(FakeMediaNormalizer(video_probe()))

    with pytest.raises(MediaLimitExceededError) as raised:
        service.validate_batch(MediaBatchTotals(image_count=11, video_count=3))

    assert raised.value.violation.measure is MediaLimitMeasure.IMAGE_COUNT


async def test_normalizer_output_budget_is_checked_before_result_is_returned(tmp_path: Path) -> None:
    normalizer = FakeMediaNormalizer(video_probe())
    normalizer.output_bytes = 101
    service = MediaNormalizationService(normalizer, limits=MediaLimits(max_output_bytes=100))

    with pytest.raises(MediaLimitExceededError) as raised:
        await service.normalize(request(tmp_path))

    assert raised.value.violation.measure is MediaLimitMeasure.OUTPUT_BYTES
    assert len(normalizer.discard_calls) == 1


async def test_symlink_sources_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.mp4"
    target.write_bytes(b"video")
    source = tmp_path / "source.mp4"
    source.symlink_to(target)
    normalizer = FakeMediaNormalizer(video_probe())
    service = MediaNormalizationService(normalizer)
    command = MediaNormalizationRequest(
        kind=MediaKind.VIDEO,
        source_path=source,
        output_path=tmp_path / "normalized.mp4",
        poster_path=tmp_path / "poster.jpg",
    )

    with pytest.raises(InvalidMediaError) as raised:
        await service.normalize(command)

    assert raised.value.reason is MediaFailureReason.SOURCE_NOT_REGULAR


async def test_service_closes_the_normalizer_port() -> None:
    normalizer = FakeMediaNormalizer(video_probe())

    await MediaNormalizationService(normalizer).aclose()

    assert normalizer.closed


def test_image_request_cannot_hide_a_video_option(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Image normalization"):
        MediaNormalizationRequest(
            kind=MediaKind.IMAGE,
            source_path=tmp_path / "source.png",
            output_path=tmp_path / "normalized.png",
            strip_audio=True,
        )
