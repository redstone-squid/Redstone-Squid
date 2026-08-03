"""Fixtures that build schematic bytes with the real engine.

Fixtures are generated programmatically rather than committed as binary files: a `.litematic`
of uncertain provenance in the repository is neither reviewable nor reproducible, and the
engine can produce exactly the shapes each test needs.
"""

import base64
from collections.abc import Callable

import pytest

nucleation = pytest.importorskip("nucleation", reason="requires the optional 'schematics' extra")


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
