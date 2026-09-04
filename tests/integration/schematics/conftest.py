"""Fixtures that build schematic bytes with the real engine.

Fixtures are generated programmatically rather than committed as binary files: a `.litematic`
of uncertain provenance in the repository is neither reviewable nor reproducible, and the
engine can produce exactly the shapes each test needs.
"""

import base64
import json
from collections.abc import Callable

import pytest

from squid.schematics.domain.models import SchematicFormat

nucleation = pytest.importorskip("nucleation", reason="requires the optional 'schematics' extra")


@pytest.fixture
def native_format_exports() -> dict[SchematicFormat, bytes]:
    """Return every supported format the pinned native wheel can generate itself."""
    schematic = nucleation.Schematic.create("format-round-trip")
    schematic.set_block(0, 0, 0, "minecraft:stone")
    return {
        SchematicFormat.LITEMATIC: base64.b64decode(schematic.to_litematic_b64()),
        SchematicFormat.SPONGE_SCHEM: base64.b64decode(schematic.to_schematic_b64()),
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
