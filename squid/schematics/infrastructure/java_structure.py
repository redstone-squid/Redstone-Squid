"""Bounded decoding for vanilla Java structure-block NBT files."""

import math
import struct
import zlib
from dataclasses import dataclass

from squid.schematics.domain.formats import sniff_container
from squid.schematics.domain.models import SchematicLimits, Vector3

type NbtValue = int | float | str | bytes | list[NbtValue] | dict[str, NbtValue]

_MAX_NBT_DEPTH = 32
_MAX_NBT_VALUES = 4_000_000


class JavaStructureDecodeError(ValueError):
    """A vanilla Java structure file violates its bounded NBT/schema contract."""


@dataclass(frozen=True, slots=True)
class JavaStructureBlock:
    """One palette-indexed block in a decoded Java structure."""

    position: Vector3
    state: str
    nbt: dict[str, NbtValue] | None = None


@dataclass(frozen=True, slots=True)
class JavaStructureEntity:
    """One entity in a decoded Java structure."""

    entity_id: str
    position: tuple[float, float, float]
    nbt: dict[str, NbtValue]


@dataclass(frozen=True, slots=True)
class JavaStructure:
    """The Java structure facts needed to construct a native engine schematic."""

    data_version: int
    size: Vector3
    blocks: tuple[JavaStructureBlock, ...]
    entities: tuple[JavaStructureEntity, ...]


def decode_java_structure(data: bytes, limits: SchematicLimits) -> JavaStructure:
    """Decode a raw or compressed Java structure within explicit byte, depth, and value budgets."""
    payload = _inflate(data, limits.max_inflated_bytes)
    root = _NbtReader(payload, max_values=_MAX_NBT_VALUES).read_root()

    data_version = _integer(root.get("DataVersion"), "DataVersion")
    if data_version < 0:
        msg = "DataVersion must be non-negative"
        raise JavaStructureDecodeError(msg)

    size = _vector(root.get("size"), "size")
    if any(axis <= 0 for axis in size):
        msg = "size axes must be positive"
        raise JavaStructureDecodeError(msg)
    if max(size) > limits.max_axis_length:
        msg = "size exceeds the configured axis limit"
        raise JavaStructureDecodeError(msg)
    volume = size[0] * size[1] * size[2]
    if volume > limits.max_allocated_volume:
        msg = "size exceeds the configured volume limit"
        raise JavaStructureDecodeError(msg)

    raw_palette = _list(root.get("palette"), "palette")
    if not raw_palette:
        msg = "palette must not be empty"
        raise JavaStructureDecodeError(msg)
    palette = tuple(_palette_state(entry, index) for index, entry in enumerate(raw_palette))

    raw_blocks = _list(root.get("blocks"), "blocks")
    if len(raw_blocks) > volume:
        msg = "blocks contains more entries than the declared volume"
        raise JavaStructureDecodeError(msg)
    blocks: list[JavaStructureBlock] = []
    occupied: set[Vector3] = set()
    for index, raw_block in enumerate(raw_blocks):
        block = _compound(raw_block, f"blocks[{index}]")
        position = _vector(block.get("pos"), f"blocks[{index}].pos")
        if any(coordinate < 0 or coordinate >= axis for coordinate, axis in zip(position, size, strict=True)):
            msg = f"blocks[{index}].pos lies outside size"
            raise JavaStructureDecodeError(msg)
        if position in occupied:
            msg = f"blocks[{index}].pos duplicates another block"
            raise JavaStructureDecodeError(msg)
        occupied.add(position)
        state_index = _integer(block.get("state"), f"blocks[{index}].state")
        if not 0 <= state_index < len(palette):
            msg = f"blocks[{index}].state is outside palette"
            raise JavaStructureDecodeError(msg)
        raw_nbt = block.get("nbt")
        block_nbt = None if raw_nbt is None else _compound(raw_nbt, f"blocks[{index}].nbt")
        blocks.append(JavaStructureBlock(position=position, state=palette[state_index], nbt=block_nbt))

    raw_entities = _list(root.get("entities"), "entities")
    entities: list[JavaStructureEntity] = []
    for index, raw_entity in enumerate(raw_entities):
        entity = _compound(raw_entity, f"entities[{index}]")
        raw_position = _list(entity.get("pos"), f"entities[{index}].pos")
        if len(raw_position) != 3:
            msg = f"entities[{index}].pos must contain exactly three numbers"
            raise JavaStructureDecodeError(msg)
        position = (
            _finite_number(raw_position[0], f"entities[{index}].pos"),
            _finite_number(raw_position[1], f"entities[{index}].pos"),
            _finite_number(raw_position[2], f"entities[{index}].pos"),
        )
        nbt = _compound(entity.get("nbt"), f"entities[{index}].nbt")
        entity_id = nbt.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            msg = f"entities[{index}].nbt.id must be a non-empty string"
            raise JavaStructureDecodeError(msg)
        entities.append(JavaStructureEntity(entity_id=entity_id, position=position, nbt=nbt))

    return JavaStructure(
        data_version=data_version,
        size=size,
        blocks=tuple(blocks),
        entities=tuple(entities),
    )


def json_nbt(value: NbtValue) -> object:
    """Translate decoded NBT into the JSON value accepted by Nucleation's mutation API."""
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, list):
        return [json_nbt(item) for item in value]
    if isinstance(value, dict):
        return {key: json_nbt(item) for key, item in value.items()}
    return value


class _NbtReader:
    def __init__(self, data: bytes, *, max_values: int) -> None:
        self._data = memoryview(data)
        self._offset = 0
        self._values_left = max_values

    def read_root(self) -> dict[str, NbtValue]:
        if self._unsigned_byte() != 10:
            msg = "NBT root must be a compound"
            raise JavaStructureDecodeError(msg)
        self._string()
        root = self._compound(0)
        if self._offset != len(self._data):
            msg = "NBT has trailing bytes"
            raise JavaStructureDecodeError(msg)
        return root

    def _payload(self, kind: int, depth: int) -> NbtValue:
        self._claim_value()
        if depth > _MAX_NBT_DEPTH:
            msg = "NBT exceeds the maximum nesting depth"
            raise JavaStructureDecodeError(msg)
        if kind == 1:
            return self._integer_value(">b", 1)
        if kind == 2:
            return self._integer_value(">h", 2)
        if kind == 3:
            return self._integer_value(">i", 4)
        if kind == 4:
            return self._integer_value(">q", 8)
        if kind == 5:
            return self._float_value(">f", 4)
        if kind == 6:
            return self._float_value(">d", 8)
        if kind == 7:
            length = self._length("byte array")
            return bytes(self._take(length))
        if kind == 8:
            return self._string()
        if kind == 9:
            item_kind = self._unsigned_byte()
            length = self._length("list")
            self._claim_values(length)
            return [self._payload_without_claim(item_kind, depth + 1) for _ in range(length)]
        if kind == 10:
            return self._compound(depth + 1)
        if kind == 11:
            return self._array(">i", 4, "int array")
        if kind == 12:
            return self._array(">q", 8, "long array")
        msg = f"NBT contains unknown tag type {kind}"
        raise JavaStructureDecodeError(msg)

    def _payload_without_claim(self, kind: int, depth: int) -> NbtValue:
        self._values_left += 1
        return self._payload(kind, depth)

    def _compound(self, depth: int) -> dict[str, NbtValue]:
        if depth > _MAX_NBT_DEPTH:
            msg = "NBT exceeds the maximum nesting depth"
            raise JavaStructureDecodeError(msg)
        result: dict[str, NbtValue] = {}
        while True:
            kind = self._unsigned_byte()
            if kind == 0:
                return result
            name = self._string()
            if name in result:
                msg = f"NBT compound repeats key {name!r}"
                raise JavaStructureDecodeError(msg)
            result[name] = self._payload(kind, depth)

    def _array(self, pattern: str, width: int, label: str) -> list[NbtValue]:
        length = self._length(label)
        self._claim_values(length)
        raw = self._take(length * width)
        return [item[0] for item in struct.iter_unpack(pattern, raw)]

    def _length(self, label: str) -> int:
        length = self._integer_value(">i", 4)
        if length < 0:
            msg = f"NBT {label} has a negative length"
            raise JavaStructureDecodeError(msg)
        return length

    def _string(self) -> str:
        length = self._integer_value(">H", 2)
        try:
            return bytes(self._take(length)).decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "NBT contains invalid UTF-8"
            raise JavaStructureDecodeError(msg) from exc

    def _unsigned_byte(self) -> int:
        return self._integer_value(">B", 1)

    def _integer_value(self, pattern: str, width: int) -> int:
        value = struct.unpack(pattern, self._take(width))[0]
        assert isinstance(value, int)
        return value

    def _float_value(self, pattern: str, width: int) -> float:
        value = struct.unpack(pattern, self._take(width))[0]
        assert isinstance(value, float)
        return value

    def _take(self, length: int) -> memoryview:
        end = self._offset + length
        if end > len(self._data):
            msg = "NBT is truncated"
            raise JavaStructureDecodeError(msg)
        value = self._data[self._offset : end]
        self._offset = end
        return value

    def _claim_value(self) -> None:
        self._claim_values(1)

    def _claim_values(self, count: int) -> None:
        if count > self._values_left:
            msg = "NBT exceeds the maximum decoded-value budget"
            raise JavaStructureDecodeError(msg)
        self._values_left -= count


def _inflate(data: bytes, limit: int) -> bytes:
    container = sniff_container(data)
    if container == "raw-nbt":
        if len(data) > limit:
            msg = "NBT exceeds the inflated-byte limit"
            raise JavaStructureDecodeError(msg)
        return data
    if container not in ("gzip", "zlib"):
        msg = "Java structures must contain raw, gzip, or zlib NBT"
        raise JavaStructureDecodeError(msg)
    decompressor = zlib.decompressobj(31 if container == "gzip" else 15)
    try:
        payload = decompressor.decompress(data, limit + 1)
    except zlib.error as exc:
        msg = "Java structure compression is invalid"
        raise JavaStructureDecodeError(msg) from exc
    if len(payload) > limit:
        msg = "NBT exceeds the inflated-byte limit"
        raise JavaStructureDecodeError(msg)
    if not decompressor.eof:
        msg = "Java structure compression is truncated or exceeds the limit"
        raise JavaStructureDecodeError(msg)
    if decompressor.unused_data:
        msg = "Java structure compression has trailing data"
        raise JavaStructureDecodeError(msg)
    return payload


def _compound(value: NbtValue | None, label: str) -> dict[str, NbtValue]:
    if not isinstance(value, dict):
        msg = f"{label} must be a compound"
        raise JavaStructureDecodeError(msg)
    return value


def _list(value: NbtValue | None, label: str) -> list[NbtValue]:
    if not isinstance(value, list):
        msg = f"{label} must be a list"
        raise JavaStructureDecodeError(msg)
    return value


def _integer(value: NbtValue | None, label: str) -> int:
    if not isinstance(value, int):
        msg = f"{label} must be an integer"
        raise JavaStructureDecodeError(msg)
    return value


def _vector(value: NbtValue | None, label: str) -> Vector3:
    values = _list(value, label)
    if len(values) != 3:
        msg = f"{label} must contain exactly three integers"
        raise JavaStructureDecodeError(msg)
    return (_integer(values[0], label), _integer(values[1], label), _integer(values[2], label))


def _finite_number(value: NbtValue, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        msg = f"{label} must contain only finite numbers"
        raise JavaStructureDecodeError(msg)
    return float(value)


def _palette_state(value: NbtValue, index: int) -> str:
    entry = _compound(value, f"palette[{index}]")
    name = entry.get("Name")
    if not isinstance(name, str) or not name:
        msg = f"palette[{index}].Name must be a non-empty string"
        raise JavaStructureDecodeError(msg)
    raw_properties = entry.get("Properties")
    if raw_properties is None:
        return name
    properties = _compound(raw_properties, f"palette[{index}].Properties")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in properties.items()):
        msg = f"palette[{index}].Properties must contain only strings"
        raise JavaStructureDecodeError(msg)
    rendered = ",".join(f"{key}={properties[key]}" for key in sorted(properties))
    return f"{name}[{rendered}]"


__all__ = [
    "JavaStructure",
    "JavaStructureBlock",
    "JavaStructureDecodeError",
    "JavaStructureEntity",
    "decode_java_structure",
    "json_nbt",
]
