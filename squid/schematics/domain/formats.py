"""Container and format detection for uploaded schematic files.

Standard-library only, and it runs on attacker-controlled bytes straight off a Discord
attachment, *before* anything reaches the native engine. Every entry point either returns a
value or raises a :mod:`squid.schematics.errors` exception, and none materialises more than
the caller's budget.

Detection is content-first: a filename extension is consulted only when the bytes are a valid
NBT stream whose root compound carries none of the marker names we recognise, so renaming
`bomb.gz` to `door.litematic` buys an attacker nothing.
"""

import zlib
from collections.abc import Mapping
from typing import Literal

from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import DecompressionBudgetExceededError, InvalidSchematicError

type Container = Literal["gzip", "zlib", "zip", "raw-nbt", "unknown"]

_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_TAG_COMPOUND = 0x0A

_CHUNK_BYTES = 64 * 1024
_GZIP_WBITS = 31
_ZLIB_WBITS = 15

SCHEMATIC_EXTENSIONS: Mapping[str, SchematicFormat] = {
    ".litematic": SchematicFormat.LITEMATIC,
    ".schem": SchematicFormat.SPONGE_SCHEM,
    ".schematic": SchematicFormat.LEGACY_SCHEMATIC,
    ".nbt": SchematicFormat.STRUCTURE_NBT,
    ".mcstructure": SchematicFormat.MCSTRUCTURE,
}


def format_from_filename(filename: str) -> SchematicFormat | None:
    """Return the format a filename claims, or `None` if its extension is not one of ours.

    Case-insensitive, and it looks only at the final extension. This is a *claim*, never a
    verdict; pair it with :func:`sniff_schematic_format` before trusting it.
    """
    lowered = filename.lower()
    for extension, schematic_format in SCHEMATIC_EXTENSIONS.items():
        if lowered.endswith(extension):
            return schematic_format
    return None


def sniff_container(data: bytes) -> Container:
    """Classify the outer wrapper of `data` from its magic bytes alone.

    `"raw-nbt"` is a shape check, not a validation: a root `TAG_Compound` followed by a name
    length that fits inside the data. Both `.mcstructure` (little-endian) and Java structure
    files land there.
    """
    if data.startswith(_GZIP_MAGIC):
        return "gzip"
    if any(data.startswith(magic) for magic in _ZIP_MAGICS):
        return "zip"
    if _looks_like_zlib(data):
        return "zlib"
    if _looks_like_raw_nbt(data):
        return "raw-nbt"
    return "unknown"


def inflated_size_at_most(data: bytes, limit: int) -> int:
    """Return the inflated size of `data`, refusing to inflate more than `limit` bytes.

    Decompression is streamed in bounded chunks and abandoned the moment the budget is
    exceeded, so a bomb costs us `limit` bytes of scratch memory rather than the gigabytes it
    wanted. Uncompressed NBT is its own inflated size.

    Raises:
        DecompressionBudgetExceededError: the stream inflates past `limit`.
        InvalidSchematicError: the stream is corrupt, truncated, or not a container this
            application accepts. Zip archives are rejected here: world archives are out of
            scope, and honouring their declared sizes would mean trusting attacker-written
            headers.
    """
    container = sniff_container(data)
    if container == "gzip":
        return _streamed_inflated_size(data, limit, wbits=_GZIP_WBITS)
    if container == "zlib":
        return _streamed_inflated_size(data, limit, wbits=_ZLIB_WBITS)
    if container == "raw-nbt":
        if len(data) > limit:
            raise DecompressionBudgetExceededError(limit=limit)
        return len(data)
    if container == "zip":
        raise InvalidSchematicError(
            context={"container": container},
            developer_action="World archives are out of scope; reject them before this call.",
        )
    raise InvalidSchematicError(context={"container": container})


def sniff_schematic_format(
    data: bytes,
    *,
    filename_hint: str | None = None,
    max_sniff_bytes: int = 64 * 1024,
) -> SchematicFormat | None:
    """Identify the schematic format of `data`, or `None` if it is not one we read.

    Only the first `max_sniff_bytes` of inflated output are examined; every supported format
    names its top-level members in the root compound, so truncation past that is expected
    rather than an error.

    `filename_hint` is a last resort, consulted only once the bytes are known to be a real NBT
    stream: an unrecognised root compound is worth handing to the engine, arbitrary garbage
    named `.litematic` is not.
    """
    container = sniff_container(data)
    if container == "zip" or container == "unknown":
        return None

    prefix = _inflated_prefix(data, max_sniff_bytes, container=container)
    if not _looks_like_raw_nbt(prefix):
        return None

    # Litematica nests everything under Metadata/Regions, and is checked first because its
    # region compounds also contain a BlockStatePalette.
    if _names_present(prefix, ("Regions", "Metadata")):
        return SchematicFormat.LITEMATIC
    # Sponge v1/v2 put Palette at the root, v3 under a Schematic compound. Either way the
    # capitalised name appears, and legacy MCEdit files have no palette at all.
    if _names_present(prefix, ("Palette",)):
        return SchematicFormat.SPONGE_SCHEM
    if _names_present(prefix, ("Blocks", "Data")):
        return SchematicFormat.LEGACY_SCHEMATIC
    # Vanilla structure blocks use lowercase member names.
    if _names_present(prefix, ("blocks", "palette", "size")):
        return SchematicFormat.STRUCTURE_NBT
    # Bedrock writes little-endian NBT.
    if _names_present(prefix, ("size", "structure"), little_endian=True):
        return SchematicFormat.MCSTRUCTURE

    return format_from_filename(filename_hint) if filename_hint else None


def _looks_like_zlib(data: bytes) -> bool:
    if len(data) < 2:
        return False
    cmf, flg = data[0], data[1]
    return cmf & 0x0F == 8 and (cmf << 8 | flg) % 31 == 0


def _looks_like_raw_nbt(data: bytes) -> bool:
    if len(data) < 3 or data[0] != _TAG_COMPOUND:
        return False
    # The root name length is two bytes; accept either endianness, since Bedrock writes
    # little-endian NBT and both readings must fit within the data for the file to be usable.
    big = int.from_bytes(data[1:3], "big")
    little = int.from_bytes(data[1:3], "little")
    return min(big, little) + 3 <= len(data)


def _names_present(payload: bytes, names: tuple[str, ...], *, little_endian: bool = False) -> bool:
    """Report whether every name appears in `payload` as an NBT length-prefixed string.

    Requiring the two-byte length immediately before the name keeps this from firing on block
    identifiers and sign text containing the same word.
    """
    byte_order: Literal["little", "big"] = "little" if little_endian else "big"
    return all(
        len(encoded).to_bytes(2, byte_order) + encoded in payload
        for encoded in (name.encode("utf-8") for name in names)
    )


def _streamed_inflated_size(data: bytes, limit: int, *, wbits: int) -> int:
    total = 0
    decompressor = zlib.decompressobj(wbits)
    pending = memoryview(data)

    while pending or decompressor.unconsumed_tail:
        if decompressor.unconsumed_tail:
            feed: memoryview | bytes = decompressor.unconsumed_tail
        else:
            feed, pending = pending[:_CHUNK_BYTES], pending[_CHUNK_BYTES:]
        try:
            total += len(decompressor.decompress(feed, _CHUNK_BYTES))
        except zlib.error as exc:
            raise InvalidSchematicError(context={"reason": "corrupt compressed stream"}) from exc
        if total > limit:
            raise DecompressionBudgetExceededError(limit=limit)
        if decompressor.eof:
            return total

    raise InvalidSchematicError(context={"reason": "truncated compressed stream"})


def _inflated_prefix(data: bytes, size: int, *, container: Container) -> bytes:
    """Return at most `size` bytes of inflated output, answering `b""` for an unreadable stream.

    Sniffing only: a partial read is the normal case, and a corrupt tail arrives after the root
    compound has already been seen.
    """
    if container == "raw-nbt":
        return data[:size]

    wbits = _GZIP_WBITS if container == "gzip" else _ZLIB_WBITS
    try:
        return zlib.decompressobj(wbits).decompress(data, size)
    except zlib.error:
        return b""


__all__ = [
    "SCHEMATIC_EXTENSIONS",
    "Container",
    "format_from_filename",
    "inflated_size_at_most",
    "sniff_container",
    "sniff_schematic_format",
]
