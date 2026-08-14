"""Public schematic metadata and content routes."""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from squid.api.dependencies import BuildQueries, Schematics
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page
from squid.api.v1.schemas.schematics import SchematicSummary
from squid.builds.domain import Status
from squid.builds.errors import BuildNotFoundError
from squid.core.pagination import offset_page

router = APIRouter(tags=["schematics"])


@router.get(
    "/builds/{build_id}/schematics",
    response_model=Page[SchematicSummary],
    responses=responses(400, 404, 422, 503),
)
async def list_build_schematics(
    build_id: int,
    build_queries: BuildQueries,
    schematics: Schematics,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[SchematicSummary]:
    """List analyzed schematics attached to a confirmed build."""
    build = await build_queries.get(build_id)
    if build is None or build.submission_status is not Status.CONFIRMED:
        raise BuildNotFoundError(build_id)
    stored = await schematics.list_public_for_build(build_id)
    page = offset_page(stored, offset=offset or 0, page_size=page_size)
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
    build = await build_queries.get(build_id)
    if build is None or build.submission_status is not Status.CONFIRMED:
        raise BuildNotFoundError(build_id)
    content, stored = await schematics.public_content(build_id, schematic_id)
    publication = stored.publication
    assert publication.license is not None
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "public, max-age=300, must-revalidate",
            "Content-Disposition": f'attachment; filename="build-{build_id}-schematic-{schematic_id}.schem"',
            "Link": f'<{publication.license.uri}>; rel="license"',
            "X-Content-Type-Options": "nosniff",
            "X-Schematic-License": publication.license.value,
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
