"""Inference previews decode ordinary MP4 attachments, including trailing indexes."""

import shutil
from pathlib import Path

import anyio
import pytest

from squid.bot.utils.web import extract_video_preview

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="requires ffmpeg and ffprobe"
)


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

    frame = await extract_video_preview(data)

    assert frame.getvalue().startswith(b"\x89PNG\r\n\x1a\n")


async def test_invalid_video_reports_decoder_error() -> None:
    with pytest.raises(RuntimeError, match=r"Video frame extraction failed \(exit \d+\): .+"):
        await extract_video_preview(b"not a video")


@pytest.mark.parametrize("single_frame", [False, True])
async def test_preview_uses_middle_content_and_handles_single_frame_clips(tmp_path: Path, single_frame: bool) -> None:
    source = tmp_path / "recording.mp4"
    content = (
        "color=c=lime:s=320x240:r=10"
        if single_frame
        else "color=c=red:s=320x240:r=10:d=0.5[a];"
        "color=c=lime:s=320x240:r=10:d=1[b];"
        "color=c=red:s=320x240:r=10:d=0.5[c];[a][b][c]concat=n=3:v=1:a=0"
    )
    await anyio.run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            content,
            *(["-frames:v", "1"] if single_frame else []),
            "-c:v",
            "mpeg4",
            str(source),
        ]
    )

    frame = await extract_video_preview(source.read_bytes())

    pixel = await anyio.run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-vf",
            "crop=1:1:160:120",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        input=frame.getvalue(),
    )
    red, green, blue = pixel.stdout
    assert green > 200
    assert red < 50
    assert blue < 50
