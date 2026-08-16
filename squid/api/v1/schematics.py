"""Public schematic metadata and content routes."""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from squid.api.dependencies import BuildQueries, Schematics
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page, resolve_selector
from squid.api.v1.schemas.schematics import SchematicSummary

router = APIRouter(tags=["schematics"])


@router.get(
    "/builds/{build_id}/schematics",
    response_model=Page[SchematicSummary],
    responses=responses(
        400,
        404,
        422,
        503,
        describe={404: "No confirmed build with this identifier. A pending build answers 404 as well."},
    ),
)
async def list_build_schematics(
    build_id: int,
    build_queries: BuildQueries,
    schematics: Schematics,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[SchematicSummary]:
    """List analyzed schematics attached to a confirmed build."""
    await build_queries.get_public(build_id)
    # Attachment order has no identifier sequence worth anchoring to, so this listing is
    # offset-only, like the ranked build search.
    selector = resolve_selector(offset=offset, after_id=None, before_id=None, keyset_allowed=False)
    page = await schematics.list_public_page(build_id, selector=selector, page_size=page_size)
    return render_page(page, SchematicSummary.from_domain)


@router.get(
    "/builds/{build_id}/schematics/{schematic_id}/content",
    response_class=Response,
    responses={
        **responses(404, 422, 503),
        200: {"content": {"application/octet-stream": {}}, "description": "Stored schematic content"},
    },
)
async def get_schematic_content(
    build_id: int,
    schematic_id: Annotated[int, Path(ge=1)],
    build_queries: BuildQueries,
    schematics: Schematics,
) -> Response:
    """Download explicitly published sanitized bytes from a confirmed build."""
    await build_queries.get_public(build_id)
    download = await schematics.public_download(build_id, schematic_id)
    # The stem stays server-generated so no user-supplied filename reaches a response
    # header; only the extension follows the stored container, which used to be `.schem`
    # for all five formats.
    filename = f"build-{build_id}-schematic-{schematic_id}.{download.source_format.value}"
    return Response(
        content=download.content,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "public, max-age=300, must-revalidate",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Link": f'<{download.license.uri}>; rel="license"',
            "X-Content-Type-Options": "nosniff",
        },
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
    recipe_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], schematics: Schematics
) -> Response:
    """Return a content-addressed PNG preview from private object storage."""
    content = await schematics.render_content(recipe_hash)
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
