"""Exercise the native helper, worker protocol, and FFmpeg together."""

import base64
import io
import json
import shutil
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import nucleation
import pytest

from squid.config import SchematicConfig
from squid.schematics.application.video import VideoInteraction, VideoRenderRequest
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.infrastructure.worker import SchematicWorkerPool

pytestmark = pytest.mark.schematic


def _test_pack() -> VerifiedResourcePack:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 16, 16, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\xa0\x80\xff" * 16) * 16))
        + chunk(b"IEND", b"")
    )
    model = {
        "textures": {"all": "minecraft:block/test"},
        "elements": [
            {
                "from": [0, 0, 0],
                "to": [16, 16, 16],
                "faces": {face: {"texture": "#all"} for face in ("up", "down", "north", "south", "east", "west")},
            }
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pack.mcmeta", '{"pack":{"pack_format":15,"description":"Test cubes"}}')
        archive.writestr("assets/minecraft/models/block/test.json", json.dumps(model))
        archive.writestr("assets/minecraft/textures/block/test.png", png)
        for block in ("stone", "sticky_piston", "piston_head", "lever"):
            archive.writestr(
                f"assets/minecraft/blockstates/{block}.json",
                json.dumps(
                    {
                        "variants": {"": {"model": "minecraft:block/test"}},
                    }
                ),
            )
    return VerifiedResourcePack.from_bytes(buffer.getvalue())


async def test_worker_returns_a_playable_simulation_video(tmp_path: Path) -> None:
    helper = Path("native/schematic-video/target/debug/squid-schematic-video").resolve()
    if not helper.is_file() or not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        pytest.skip("Build the native video helper and install FFmpeg to run this test.")
    schematic = nucleation.Schematic.create("video test")
    for position, state in (
        ((0, 0, 0), "minecraft:stone"),
        ((0, 1, 0), "minecraft:lever[face=floor,facing=east,powered=false]"),
        ((1, 0, 0), "minecraft:sticky_piston[facing=east,extended=false]"),
        ((2, 0, 0), "minecraft:stone"),
    ):
        schematic.set_block_from_string(*position, state)
    data = base64.b64decode(schematic.to_schematic_b64())
    request = VideoRenderRequest(
        width=320,
        height=240,
        duration_ticks=40,
        interactions=(
            VideoInteraction(tick=5, position=(0, 1, 0)),
            VideoInteraction(tick=25, position=(0, 1, 0)),
        ),
    )
    pool = SchematicWorkerPool(SchematicConfig(workers=1, video_helper_path=helper))
    async with pool.running():
        content = await pool.render_video(data, request=request, resource_pack=_test_pack())
        assert (await pool.capabilities()).available
    output = tmp_path / "video.mp4"
    output.write_bytes(content)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,nb_frames:format=duration",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)
    assert metadata["streams"][0] == {"codec_name": "h264", "width": 320, "height": 240, "nb_frames": "60"}
    assert float(metadata["format"]["duration"]) == 2.0
