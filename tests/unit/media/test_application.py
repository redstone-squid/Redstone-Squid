from pathlib import Path

import pytest

from squid.media.application import (
    MediaProcessingRequest,
    MediaProcessingResult,
    MediaProcessingService,
)
from squid.media.domain import (
    MediaArtifact,
    MediaBatchTotals,
    MediaKind,
    MediaLimitMeasure,
    MediaLimits,
    MediaProbe,
    MediaProcessingAction,
    MediaProcessingReport,
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


class FakeMediaProcessor:
    """Return probe facts and reports without writing outputs; optionally mutate the source file."""

    def __init__(self, inspected: MediaProbe) -> None:
        self.inspected = inspected
        self.probe_calls: list[Path] = []
        self.normalize_calls: list[tuple[MediaProcessingRequest, int, MediaLimits]] = []
        self.discard_calls: list[MediaProcessingResult] = []
        self.closed = False
        self.change_on_probe = False
        self.change_on_normalize = False
        self.output_bytes = 100

    async def probe(self, source_path: Path) -> MediaProbe:
        self.probe_calls.append(source_path)
        if self.change_on_probe:
            source_path.write_bytes(b"changed during probe")
        return self.inspected

    async def process(
        self,
        request: MediaProcessingRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaProcessingResult:
        self.normalize_calls.append((request, source_bytes, limits))
        if self.change_on_normalize:
            request.source_path.write_bytes(b"changed during processing")
        poster = artifact(content_type="image/jpeg", byte_size=10) if request.poster_path is not None else None
        report = MediaProcessingReport(
            kind=request.kind,
            source_bytes=source_bytes,
            input_probe=probe,
            output_probe=probe,
            output=artifact(byte_size=self.output_bytes),
            poster=poster,
            actions=(MediaProcessingAction.METADATA_REMOVED,),
        )
        return MediaProcessingResult(
            output_path=request.output_path,
            poster_path=request.poster_path,
            report=report,
        )

    async def aclose(self) -> None:
        self.closed = True

    async def discard(self, result: MediaProcessingResult) -> None:
        self.discard_calls.append(result)


def request(tmp_path: Path) -> MediaProcessingRequest:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return MediaProcessingRequest(
        kind=MediaKind.VIDEO,
        source_path=source,
        output_path=tmp_path / "normalized.mp4",
        poster_path=tmp_path / "poster.jpg",
    )


async def test_service_validates_then_delegates_to_the_processor(tmp_path: Path) -> None:
    processor = FakeMediaProcessor(video_probe())
    service = MediaProcessingService(processor)
    command = request(tmp_path)

    result = await service.process(command)

    assert result.report.source_bytes == len(b"video")
    assert processor.probe_calls == [command.source_path]
    assert processor.normalize_calls == [(command, len(b"video"), service.limits)]


async def test_source_byte_budget_is_checked_before_probe(tmp_path: Path) -> None:
    processor = FakeMediaProcessor(video_probe())
    service = MediaProcessingService(processor, limits=MediaLimits(max_source_bytes=4))
    command = request(tmp_path)

    with pytest.raises(MediaLimitExceededError) as raised:
        await service.process(command)

    assert raised.value.violations[0].measure is MediaLimitMeasure.SOURCE_BYTES
    assert processor.probe_calls == []


async def test_source_changes_between_probe_and_decode_are_rejected(tmp_path: Path) -> None:
    processor = FakeMediaProcessor(video_probe())
    processor.change_on_probe = True
    service = MediaProcessingService(processor)

    with pytest.raises(InvalidMediaError) as raised:
        await service.process(request(tmp_path))

    assert raised.value.reason is MediaFailureReason.SOURCE_CHANGED
    assert processor.discard_calls == []
    assert processor.normalize_calls == []


async def test_source_changes_during_decode_are_rejected(tmp_path: Path) -> None:
    processor = FakeMediaProcessor(video_probe())
    processor.change_on_normalize = True
    service = MediaProcessingService(processor)

    with pytest.raises(InvalidMediaError) as raised:
        await service.process(request(tmp_path))

    assert raised.value.reason is MediaFailureReason.SOURCE_CHANGED
    assert len(processor.discard_calls) == 1


def test_submission_wide_image_and_video_counts_are_enforced() -> None:
    service = MediaProcessingService(FakeMediaProcessor(video_probe()))

    with pytest.raises(MediaLimitExceededError) as raised:
        service.validate_batch(MediaBatchTotals(image_count=11, video_count=3))

    assert raised.value.violations[0].measure is MediaLimitMeasure.IMAGE_COUNT


async def test_processor_output_budget_is_checked_before_result_is_returned(tmp_path: Path) -> None:
    processor = FakeMediaProcessor(video_probe())
    processor.output_bytes = 101
    service = MediaProcessingService(processor, limits=MediaLimits(max_output_bytes=100))

    with pytest.raises(MediaLimitExceededError) as raised:
        await service.process(request(tmp_path))

    assert raised.value.violations[0].measure is MediaLimitMeasure.OUTPUT_BYTES
    assert len(processor.discard_calls) == 1


async def test_symlink_sources_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.mp4"
    target.write_bytes(b"video")
    source = tmp_path / "source.mp4"
    # Some platforms require extra privileges to create the symlink needed by this test.
    try:
        source.symlink_to(target)
    except OSError as error:
        pytest.skip(f"creating symlinks is not permitted here: {error}")
    processor = FakeMediaProcessor(video_probe())
    service = MediaProcessingService(processor)
    command = MediaProcessingRequest(
        kind=MediaKind.VIDEO,
        source_path=source,
        output_path=tmp_path / "normalized.mp4",
        poster_path=tmp_path / "poster.jpg",
    )

    with pytest.raises(InvalidMediaError) as raised:
        await service.process(command)

    assert raised.value.reason is MediaFailureReason.SOURCE_NOT_REGULAR


async def test_service_closes_the_processor_port() -> None:
    processor = FakeMediaProcessor(video_probe())

    await MediaProcessingService(processor).aclose()

    assert processor.closed


@pytest.mark.parametrize(
    "inspected",
    [
        video_probe(width=321),
        video_probe(duration_milliseconds=None),
        video_probe(fps_numerator=0),
        video_probe(fps_numerator=1000),
    ],
)
async def test_original_video_does_not_require_encoder_constraints(tmp_path: Path, inspected: MediaProbe) -> None:
    processor = FakeMediaProcessor(inspected)
    await MediaProcessingService(processor).process(request(tmp_path))
    assert len(processor.normalize_calls) == 1
