"""Inference previews decode ordinary MP4 attachments, including trailing indexes."""

import shutil
from pathlib import Path

import anyio
import pytest

from squid.bot.utils.web import extract_first_frame

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires ffmpeg")


async def test_preview_decodes_mp4_with_index_after_video_packets(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    await anyio.run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30",
            "-t",
            "2",
            "-c:v",
            "mpeg4",
            str(source),
        ]
    )
    data = source.read_bytes()
    assert data.index(b"mdat") < data.index(b"moov")
    assert len(data) > 32768

    frame = await extract_first_frame(data)

    assert frame.getvalue().startswith(b"\x89PNG\r\n\x1a\n")


async def test_invalid_video_reports_decoder_error() -> None:
    with pytest.raises(RuntimeError, match=r"Video frame extraction failed \(exit \d+\): .+"):
        await extract_first_frame(b"not a video")
