"""Fixtures that build schematic bytes with the real engine.

Fixtures are generated programmatically rather than committed as binary files: a `.litematic`
of uncertain provenance in the repository is neither reviewable nor reproducible, and the
engine can produce exactly the shapes each test needs.
"""

import base64
import gzip
import json
import struct
from collections.abc import Callable

import pytest

from squid.schematics.domain.models import SchematicFormat

nucleation = pytest.importorskip("nucleation", reason="requires the optional 'schematics' extra")


def _nbt_tag(kind: int, name: str, payload: bytes) -> bytes:
    encoded_name = name.encode()
    return bytes((kind,)) + struct.pack(">H", len(encoded_name)) + encoded_name + payload


def _nbt_string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack(">H", len(encoded)) + encoded


def _nbt_list(kind: int, values: list[bytes]) -> bytes:
    return bytes((kind,)) + struct.pack(">i", len(values)) + b"".join(values)


def _nbt_compound(name: str, entries: list[bytes]) -> bytes:
    return _nbt_tag(10, name, b"".join(entries) + b"\x00")


def _legacy_schematic() -> bytes:
    return _nbt_compound(
        "Schematic",
        [
            _nbt_tag(2, "Width", struct.pack(">h", 1)),
            _nbt_tag(2, "Height", struct.pack(">h", 1)),
            _nbt_tag(2, "Length", struct.pack(">h", 1)),
            _nbt_tag(8, "Materials", _nbt_string("Alpha")),
            _nbt_tag(7, "Blocks", struct.pack(">i", 1) + b"\x01"),
            _nbt_tag(7, "Data", struct.pack(">i", 1) + b"\x00"),
            _nbt_tag(9, "Entities", _nbt_list(10, [])),
            _nbt_tag(9, "TileEntities", _nbt_list(10, [])),
        ],
    )


def _structure_nbt() -> bytes:
    palette_entry = _nbt_tag(8, "Name", _nbt_string("minecraft:stone")) + b"\x00"
    block_entry = (
        _nbt_tag(9, "pos", _nbt_list(3, [struct.pack(">i", 0)] * 3))
        + _nbt_tag(3, "state", struct.pack(">i", 0))
        + b"\x00"
    )
    return _nbt_compound(
        "",
        [
            _nbt_tag(3, "DataVersion", struct.pack(">i", 3465)),
            _nbt_tag(9, "size", _nbt_list(3, [struct.pack(">i", 1)] * 3)),
            _nbt_tag(9, "palette", _nbt_list(10, [palette_entry])),
            _nbt_tag(9, "blocks", _nbt_list(10, [block_entry])),
            _nbt_tag(9, "entities", _nbt_list(10, [])),
        ],
    )


@pytest.fixture
def native_format_inputs() -> dict[SchematicFormat, bytes]:
    """Return a reviewable one-block fixture for every declared readable format."""
    schematic = nucleation.Schematic.create("format-round-trip")
    schematic.set_block(0, 0, 0, "minecraft:stone")
    return {
        SchematicFormat.LITEMATIC: base64.b64decode(schematic.to_litematic_b64()),
        SchematicFormat.SPONGE_SCHEM: base64.b64decode(schematic.to_schematic_b64()),
        SchematicFormat.LEGACY_SCHEMATIC: gzip.compress(_legacy_schematic(), mtime=0),
        SchematicFormat.STRUCTURE_NBT: _structure_nbt(),
        SchematicFormat.MCSTRUCTURE: base64.b64decode(schematic.to_mcstructure_b64()),
    }


@pytest.fixture
def periodic_door() -> Callable[..., bytes]:
    """Return a builder for a small build with a genuine 4-block period.

    Periodic on purpose so the same fixture exercises repeating-structure detection, and
    translatable so the fingerprint-invariance test has something to move.
    """

    def build(*, offset: int = 0, extra_block: bool = False) -> bytes:
        schematic = nucleation.Schematic.create("test-door")
        for column in range(6):
            for y in range(3):
                schematic.set_block_from_string(offset + column * 4, y, 0, "minecraft:stone")
            schematic.set_block_from_string(offset + column * 4 + 1, 0, 0, "minecraft:redstone_wire")
        if extra_block:
            schematic.set_block_from_string(offset + 50, 0, 0, "minecraft:glass")
        return base64.b64decode(schematic.to_litematic_b64())

    return build


@pytest.fixture
def piston_door() -> bytes:
    """A button-driven piston scene with a deterministic full cycle."""
    schematic = nucleation.Schematic.create("piston-door")
    for x in range(6):
        schematic.set_block(x, 0, 0, "minecraft:smooth_stone")
    schematic.set_block(0, 1, 0, "minecraft:oak_button[face=floor,facing=east,powered=false]")
    schematic.set_block(1, 1, 0, "minecraft:redstone_wire{simulate=true}")
    schematic.set_block(2, 1, 0, "minecraft:redstone_wire{simulate=true}")
    schematic.set_block(3, 1, 0, "minecraft:sticky_piston[facing=east,extended=false]")
    schematic.set_block(4, 1, 0, "minecraft:stone")
    return base64.b64decode(schematic.to_litematic_b64())


@pytest.fixture
def insign_piston_door(piston_door: bytes) -> bytes:
    """A two-input scene whose Insign annotation identifies the door button."""
    schematic = nucleation.Schematic.from_data(piston_door)
    schematic.set_block(5, 1, 0, "minecraft:stone_button[face=floor,facing=east,powered=false]")
    sign_nbt = {
        "Text1": '{"text":"@io.door=rc([0,-1,-1],[0,-1,-1])"}',
        "Text2": '{"text":"#io.door:type=\\"input\\""}',
        "Text3": '{"text":"#io.door:data_type=\\"bool\\""}',
        "Text4": '{"text":""}',
    }
    schematic.set_block_with_nbt(0, 2, 1, "minecraft:oak_sign[rotation=0]", json.dumps(sign_nbt))
    return base64.b64decode(schematic.to_litematic_b64())


@pytest.fixture(scope="session")
def slow_schematic() -> bytes:
    """A build large enough that analysing it takes long enough to interrupt.

    Sized so a request is reliably still running a fraction of a second in, which is what lets
    the timeout and mid-request-kill tests be deterministic rather than a race.
    """
    schematic = nucleation.Schematic.create("wide")
    for index in range(200_000):
        schematic.set_block_from_string(index % 500, (index // 500) % 500, index // 250_000, "minecraft:stone")
    return base64.b64decode(schematic.to_litematic_b64())
