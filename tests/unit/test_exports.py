import csv
import json
from collections.abc import AsyncIterator
from io import StringIO

import pytest

from squid.core import exports
from squid.core.errors import ValidationError
from squid.core.exports import ExportFormat, ExportTable, encode_export


async def _rows() -> AsyncIterator[dict[str, object]]:
    yield {"title": "=1+1", "label": "", "note": None, "attributes": ["a,b", "雪"]}
    yield {"title": "'literal", "label": "x\ny", "note": '"', "attributes": "text"}


async def test_csv_distinguishes_null_empty_text_and_json() -> None:
    columns = {"title": "string", "label": "string", "note": "string?", "attributes": "json"}
    table = ExportTable("builds", columns, _rows())
    csv_file = await encode_export([table], ExportFormat.CSV)
    first, second = csv.DictReader(StringIO(csv_file.content.decode()), quoting=csv.QUOTE_NOTNULL)
    assert (first["title"], first["label"], first["note"]) == ("'=1+1", "", None)
    assert (second["title"], second["label"], second["note"]) == ("''literal", "x\ny", '"')
    assert json.loads(first["attributes"]) == ["a,b", "雪"]
    assert json.loads(second["attributes"]) == "text"
    data = await encode_export([ExportTable("builds", columns, _rows())], ExportFormat.JSON)
    assert json.loads(data.content)["data"]["builds"] == [row async for row in _rows()]


@pytest.mark.parametrize("format", ExportFormat)
async def test_over_limit_exports_fail(format: ExportFormat, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exports, "MAX_EXPORT_BYTES", 10)
    with pytest.raises(ValidationError, match="download limit"):
        await encode_export([ExportTable("builds", {"title": "string"}, _rows())], format)
