"""Versioned tabular downloads shared by domain export queries."""

import csv
import json
from collections.abc import AsyncIterable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from tempfile import TemporaryFile
from typing import Protocol
from zipfile import ZipFile

from whenever import Instant

from squid.core.errors import ValidationError

MAX_EXPORT_BYTES = 16 * 1024 * 1024


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class ExportTable:
    """Declare the public column order and types independently of storage and encoding.

    A trailing question mark marks nullable columns. JSON columns in CSV contain a JSON value.
    Unquoted empty fields represent null. Remove one leading apostrophe from CSV text fields
    that start with one to recover the original text.
    """

    name: str
    columns: Mapping[str, str]
    rows: AsyncIterable[Mapping[str, object]]


@dataclass(frozen=True)
class Download:
    content: bytes
    extension: str
    media_type: str


class _Sink(Protocol):
    def write(self, data: bytes, /) -> int: ...


async def _csv_table(table: ExportTable, write: Callable[[str], None]) -> None:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_NOTNULL)
    writer.writerow(table.columns)
    write(buffer.getvalue())
    async for row in table.rows:
        buffer.seek(0)
        buffer.truncate()
        cells = []
        for name, kind in table.columns.items():
            value = row[name]
            if value is None:
                cells.append(None)
            elif isinstance(value, str) and kind.rstrip("?") != "json":
                # Escape leading apostrophes too, so this spreadsheet protection is reversible.
                if value.startswith(("'", "\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                cells.append(value)
            else:
                cells.append(json.dumps(value, ensure_ascii=False))
        writer.writerow(cells)
        write(buffer.getvalue())


async def _json_tables(tables: Sequence[ExportTable], write: Callable[[str], None]) -> None:
    write("{")
    for index, table in enumerate(tables):
        write(("," if index else "") + json.dumps(table.name) + ":[")
        first = True
        async for row in table.rows:
            values = {column: row[column] for column in table.columns}
            write(("" if first else ",") + json.dumps(values, ensure_ascii=False))
            first = False
        write("]")
    write("}")


async def encode_export(tables: Sequence[ExportTable], file_format: ExportFormat) -> Download:
    """Finish writing the download before delivery. CSV uses unquoted empty fields for nulls."""
    manifest = json.dumps(
        {
            "schema_version": 1,
            "exported_at": str(Instant.now()),
            "tables": {table.name: table.columns for table in tables},
        }
    )
    remaining = MAX_EXPORT_BYTES

    def limited_writer(target: _Sink) -> Callable[[str], None]:
        def write(data: str) -> None:
            nonlocal remaining
            encoded = data.encode("utf-8")
            remaining -= len(encoded)
            if remaining < 0:
                msg = "Export exceeds the 16 MiB download limit."
                raise ValidationError(msg)
            target.write(encoded)

        return write

    with TemporaryFile() as output:
        if file_format == ExportFormat.JSON:
            write = limited_writer(output)
            write('{"manifest":' + manifest + ',"data":')
            await _json_tables(tables, write)
            write("}")
            extension, media_type = "json", "application/json"
        elif len(tables) == 1:
            await _csv_table(tables[0], limited_writer(output))
            extension, media_type = "csv", "text/csv"
        else:
            with ZipFile(output, "w") as bundle:
                bundle.writestr("manifest.json", manifest)
                for table in tables:
                    with bundle.open(table.name + ".csv", "w") as member:
                        await _csv_table(table, limited_writer(member))
            extension, media_type = "zip", "application/zip"
        if output.tell() > MAX_EXPORT_BYTES:
            msg = "Export exceeds the 16 MiB download limit."
            raise ValidationError(msg)
        output.seek(0)
        return Download(output.read(), extension, media_type)
