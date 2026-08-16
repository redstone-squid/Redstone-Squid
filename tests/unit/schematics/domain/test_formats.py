"""Container and format sniffing tests.

These run on the paths that see attacker-controlled bytes first, so the properties worth
asserting are total ones: the sniffers must never raise on arbitrary input, the inflation
budget must never be exceeded, and a filename must never talk the sniffer into a format the
bytes contradict.
"""

import gzip
import zlib
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid.schematics.domain.formats import (
    Container,
    format_from_filename,
    inflated_size_at_most,
    sniff_container,
    sniff_schematic_format,
)
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import DecompressionBudgetExceededError, InvalidSchematicError

ZIP_HEADER = b"PK\x03\x04" + bytes(64)


def nbt_root(*names: str, little_endian: bool = False, root_name: str = "") -> bytes:
    """Build a minimal uncompressed NBT file whose root compound holds the given members.

    Every member is a `TAG_Byte`, which is enough for name-based sniffing and keeps the
    fixtures small enough to read.
    """
    byte_order: Literal["little", "big"] = "little" if little_endian else "big"
    encoded_root = root_name.encode("utf-8")
    out = bytearray([0x0A]) + len(encoded_root).to_bytes(2, byte_order) + encoded_root
    for name in names:
        encoded = name.encode("utf-8")
        out += bytes([0x01]) + len(encoded).to_bytes(2, byte_order) + encoded + bytes([0x00])
    return bytes(out + bytes([0x00]))


LITEMATIC_NBT = nbt_root("Version", "Metadata", "Regions", root_name="")
SPONGE_NBT = nbt_root("Version", "DataVersion", "Palette", "BlockData", "Width")
LEGACY_NBT = nbt_root("Width", "Height", "Length", "Materials", "Blocks", "Data", root_name="Schematic")
STRUCTURE_NBT = nbt_root("DataVersion", "size", "palette", "blocks", "entities")
MCSTRUCTURE_NBT = nbt_root("format_version", "size", "structure", little_endian=True)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (LITEMATIC_NBT, SchematicFormat.LITEMATIC),
        (SPONGE_NBT, SchematicFormat.SPONGE_SCHEM),
        (LEGACY_NBT, SchematicFormat.LEGACY_SCHEMATIC),
        (STRUCTURE_NBT, SchematicFormat.STRUCTURE_NBT),
        (MCSTRUCTURE_NBT, SchematicFormat.MCSTRUCTURE),
    ],
)
def test_each_format_is_recognised_gzipped_and_bare(payload: bytes, expected: SchematicFormat) -> None:
    assert sniff_schematic_format(payload) == expected
    assert sniff_schematic_format(gzip.compress(payload)) == expected
    assert sniff_schematic_format(zlib.compress(payload)) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (gzip.compress(LITEMATIC_NBT), "gzip"),
        (zlib.compress(LITEMATIC_NBT), "zlib"),
        (ZIP_HEADER, "zip"),
        (LITEMATIC_NBT, "raw-nbt"),
        (b"", "unknown"),
        (b"not a schematic at all", "unknown"),
        # A root TAG_Compound whose name length runs past the end of the data.
        (b"\x0a\xff\xfe", "unknown"),
    ],
)
def test_container_detection(data: bytes, expected: Container) -> None:
    assert sniff_container(data) == expected


def test_a_filename_never_overrides_conclusive_content() -> None:
    assert sniff_schematic_format(gzip.compress(SPONGE_NBT), filename_hint="door.litematic") == (
        SchematicFormat.SPONGE_SCHEM
    )
    assert sniff_schematic_format(MCSTRUCTURE_NBT, filename_hint="door.schem") == SchematicFormat.MCSTRUCTURE


def test_a_filename_is_used_only_when_the_bytes_are_unrecognised_nbt() -> None:
    unknown_nbt = nbt_root("SomeFutureFormatMember")

    assert sniff_schematic_format(unknown_nbt) is None
    assert sniff_schematic_format(unknown_nbt, filename_hint="door.litematic") == SchematicFormat.LITEMATIC


def test_a_schematic_extension_cannot_launder_non_nbt_content() -> None:
    disguised = gzip.compress(b"this is just text, not NBT at all")

    assert sniff_schematic_format(disguised, filename_hint="door.litematic") is None
    assert sniff_schematic_format(ZIP_HEADER, filename_hint="door.litematic") is None
    assert sniff_schematic_format(b"\x89PNG\r\n\x1a\n", filename_hint="door.mcstructure") is None


def test_sniffing_reads_no_more_than_its_budget() -> None:
    # The marker names sit at the front, so a build with a megabyte of trailing block data is
    # still identified from a small prefix.
    padded = LITEMATIC_NBT + b"\x00" * (1024 * 1024)

    assert sniff_schematic_format(gzip.compress(padded), max_sniff_bytes=256) == SchematicFormat.LITEMATIC


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("door.litematic", SchematicFormat.LITEMATIC),
        ("DOOR.LITEMATIC", SchematicFormat.LITEMATIC),
        ("my build.schem", SchematicFormat.SPONGE_SCHEM),
        ("old.schematic", SchematicFormat.LEGACY_SCHEMATIC),
        ("piece.nbt", SchematicFormat.STRUCTURE_NBT),
        ("bedrock.mcstructure", SchematicFormat.MCSTRUCTURE),
        ("screenshot.png", None),
        ("archive.litematic.zip", None),
        ("litematic", None),
        ("", None),
    ],
)
def test_format_from_filename(filename: str, expected: SchematicFormat | None) -> None:
    assert format_from_filename(filename) == expected


def test_inflated_size_is_exact() -> None:
    payload = LITEMATIC_NBT + b"\x11" * 5000

    assert inflated_size_at_most(gzip.compress(payload), 1024 * 1024) == len(payload)
    assert inflated_size_at_most(zlib.compress(payload), 1024 * 1024) == len(payload)
    assert inflated_size_at_most(payload, 1024 * 1024) == len(payload)


def test_a_decompression_bomb_is_refused() -> None:
    bomb = gzip.compress(bytes(8 * 1024 * 1024))
    limit = 64 * 1024
    assert len(bomb) < limit, "the bomb must be small compressed, or the test proves nothing"

    with pytest.raises(DecompressionBudgetExceededError) as exc_info:
        inflated_size_at_most(bomb, limit)

    assert exc_info.value.limit == limit
    assert exc_info.value.measure == "inflated size"


def test_an_uncompressed_file_over_the_budget_is_refused() -> None:
    with pytest.raises(DecompressionBudgetExceededError):
        inflated_size_at_most(nbt_root("Regions") + b"\x00" * 4096, 128)


@pytest.mark.parametrize(
    "data",
    [
        gzip.compress(LITEMATIC_NBT * 200)[:40],
        zlib.compress(LITEMATIC_NBT * 200)[:40],
    ],
)
def test_a_truncated_stream_is_invalid_rather_than_silently_short(data: bytes) -> None:
    with pytest.raises(InvalidSchematicError):
        inflated_size_at_most(data, 1024 * 1024)


def test_a_corrupt_compressed_stream_is_invalid() -> None:
    corrupt = bytearray(gzip.compress(LITEMATIC_NBT * 200))
    corrupt[20:40] = bytes(20)

    with pytest.raises(InvalidSchematicError):
        inflated_size_at_most(bytes(corrupt), 1024 * 1024)


@pytest.mark.parametrize("data", [ZIP_HEADER, b"", b"garbage"])
def test_unsupported_containers_are_rejected_before_the_engine(data: bytes) -> None:
    with pytest.raises(InvalidSchematicError):
        inflated_size_at_most(data, 1024 * 1024)


@given(data=st.binary(max_size=512))
def test_the_sniffers_are_total_over_arbitrary_bytes(data: bytes) -> None:
    sniff_container(data)
    sniff_schematic_format(data)
    sniff_schematic_format(data, filename_hint="door.litematic")


@given(data=st.binary(max_size=512), limit=st.integers(min_value=0, max_value=4096))
def test_the_inflation_budget_is_never_exceeded(data: bytes, limit: int) -> None:
    try:
        size = inflated_size_at_most(data, limit)
    except DecompressionBudgetExceededError, InvalidSchematicError:
        return

    assert size <= limit
