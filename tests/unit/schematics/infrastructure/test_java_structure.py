"""Tests for the bounded vanilla Java structure decoder."""

import gzip
import struct

import pytest

from squid.schematics.domain.models import SchematicLimits
from squid.schematics.infrastructure.java_structure import JavaStructureDecodeError, decode_java_structure


def _tag(kind: int, name: str, payload: bytes) -> bytes:
    encoded = name.encode()
    return bytes((kind,)) + struct.pack(">H", len(encoded)) + encoded + payload


def _string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack(">H", len(encoded)) + encoded


def _list(kind: int, values: list[bytes]) -> bytes:
    return bytes((kind,)) + struct.pack(">i", len(values)) + b"".join(values)


def _compound(name: str, entries: list[bytes]) -> bytes:
    return _tag(10, name, b"".join(entries) + b"\x00")


def _structure(*, size: tuple[int, int, int] = (1, 1, 1), extra: bytes = b"") -> bytes:
    properties = _tag(8, "facing", _string("east")) + b"\x00"
    palette_entry = _tag(8, "Name", _string("minecraft:oak_stairs")) + _tag(10, "Properties", properties) + b"\x00"
    block_entry = (
        _tag(9, "pos", _list(3, [struct.pack(">i", 0)] * 3)) + _tag(3, "state", struct.pack(">i", 0)) + b"\x00"
    )
    return _compound(
        "",
        [
            _tag(3, "DataVersion", struct.pack(">i", 3465)),
            _tag(9, "size", _list(3, [struct.pack(">i", axis) for axis in size])),
            _tag(9, "palette", _list(10, [palette_entry])),
            _tag(9, "blocks", _list(10, [block_entry])),
            _tag(9, "entities", _list(10, [])),
            extra,
        ],
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_decodes_raw_and_gzip_structure_nbt(compressed: bool) -> None:
    raw = _structure()

    decoded = decode_java_structure(gzip.compress(raw, mtime=0) if compressed else raw, SchematicLimits())

    assert decoded.data_version == 3465
    assert decoded.size == (1, 1, 1)
    assert decoded.blocks[0].position == (0, 0, 0)
    assert decoded.blocks[0].state == "minecraft:oak_stairs[facing=east]"


def test_rejects_inflated_data_before_parsing_it() -> None:
    raw = _structure()

    with pytest.raises(JavaStructureDecodeError, match="inflated-byte limit"):
        decode_java_structure(gzip.compress(raw, mtime=0), SchematicLimits(max_inflated_bytes=len(raw) - 1))


def test_rejects_declared_dimensions_before_constructing_blocks() -> None:
    raw = _structure(size=(2, 1, 1))

    with pytest.raises(JavaStructureDecodeError, match="axis limit"):
        decode_java_structure(raw, SchematicLimits(max_axis_length=1))


def test_rejects_nbt_beyond_the_depth_budget() -> None:
    nested = b"\x00"
    for index in range(34):
        nested = _tag(10, f"level-{index}", nested) + b"\x00"

    with pytest.raises(JavaStructureDecodeError, match="nesting depth"):
        decode_java_structure(_structure(extra=nested), SchematicLimits())


def test_rejects_declared_collections_before_allocating_them() -> None:
    oversized_list = _tag(9, "oversized", bytes((3,)) + struct.pack(">i", 4_000_001))

    with pytest.raises(JavaStructureDecodeError, match="decoded-value budget"):
        decode_java_structure(_structure(extra=oversized_list), SchematicLimits())
