"""HTTP delivery for completed data exports."""

from typing import Annotated, Any

from litestar import Response
from litestar.params import QueryParameter

from squid.core.exports import Download, ExportFormat

FormatParam = Annotated[ExportFormat, QueryParameter(name="format")]


def export_contract(*, archive: bool = False) -> dict[str, Any]:
    """Describe the two representations of a successful download."""
    tabular = "application/zip" if archive else "text/csv"
    return {
        "200": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["manifest", "data"],
                        "properties": {
                            "manifest": {"type": "object"},
                            "data": {
                                "type": "object",
                                "additionalProperties": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                    }
                },
                tabular: {"schema": {"type": "string", **({"format": "binary"} if archive else {})}},
            }
        }
    }


def export_response(name: str, download: Download) -> Response[bytes]:
    return Response(
        download.content,
        media_type=download.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="squid-{name}.{download.extension}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
