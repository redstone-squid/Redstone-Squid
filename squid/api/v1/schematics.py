"""Public schematic metadata and content routes."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from squid.api.dependencies import CursorSigner, Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.schematics import SchematicSummary
from squid.builds.domain import Status
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import ErrorCode, ValidationError

router = APIRouter(tags=["schematics"])


@router.get(
    "/builds/{build_id}/schematics",
    response_model=Page[SchematicSummary],
    responses=responses(400, 404, 422, 503),
)
async def list_build_schematics(
    build_id: int,
    services: Services,
    signer: CursorSigner,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[SchematicSummary]:
    """List analyzed schematics attached to a confirmed build."""
    build = await services.build_queries.get(build_id)
    if build is None or build.submission_status is not Status.CONFIRMED:
        raise BuildNotFoundError(build_id)
    schematics = await services.schematics.list_for_build(build_id)
    binding = f"build-schematics:{build_id}"
    offset = _offset(signer, cursor, binding)
    selected = schematics[offset : offset + page_size]
    next_offset = offset + len(selected)
    has_more = next_offset < len(schematics)
    return Page(
        items=[SchematicSummary.from_domain(schematic) for schematic in selected],
        next_cursor=signer.encode({"offset": next_offset}, binding=binding) if has_more else None,
        has_more=has_more,
    )


@router.get(
    "/schematics/{sha256}/content",
    response_class=Response,
    responses={
        **responses(404, 422, 503),
        200: {"content": {"application/octet-stream": {}}, "description": "Stored schematic content"},
    },
)
async def get_schematic_content(
    sha256: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], services: Services
) -> Response:
    """Download stored schematic bytes by their SHA-256 digest."""
    content = await services.schematics.content(sha256)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{sha256}.schematic"'},
    )


@router.get(
    "/schematic-renders/{recipe_hash}/content",
    response_class=Response,
    responses={
        **responses(404, 422, 503),
        200: {"content": {"image/png": {}}, "description": "Generated schematic preview"},
    },
)
async def get_schematic_render_content(
    recipe_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], services: Services
) -> Response:
    """Return a content-addressed PNG preview from private object storage."""
    content = await services.schematics.render_content(recipe_hash)
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _offset(signer: CursorSigner, cursor: str | None, binding: str) -> int:
    if cursor is None:
        return 0
    value = signer.decode(cursor, binding=binding).get("offset")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "cursor payload contains an invalid offset"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value
