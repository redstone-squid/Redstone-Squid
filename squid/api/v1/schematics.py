"""Public schematic metadata and content routes."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from squid.api.dependencies import BuildQueries, Schematics
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page, resolve_selector
from squid.api.v1.schemas.schematics import SchematicSummary
from squid.schematics.application.commands import MAX_RENDER_EXTENT, MIN_RENDER_EXTENT

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
    "/builds/{build_id}/schematics/render",
    response_class=Response,
    responses={
        **responses(
            404,
            409,
            422,
            503,
            describe={
                404: "No confirmed build with this identifier, or it has no schematic attached.",
                409: "The attached schematic will never be previewed; `context.reason` says why.",
                503: "Previews are not configured on this instance, or the renderer is unavailable.",
            },
        ),
        200: {"content": {"image/png": {}}, "description": "Rendered schematic preview"},
    },
)
async def render_build_schematic(
    build_id: int,
    build_queries: BuildQueries,
    schematics: Schematics,
    width: Annotated[int | None, Query(ge=MIN_RENDER_EXTENT, le=MAX_RENDER_EXTENT)] = None,
    height: Annotated[int | None, Query(ge=MIN_RENDER_EXTENT, le=MAX_RENDER_EXTENT)] = None,
    yaw: Annotated[float | None, Query(ge=-360, le=360)] = None,
    pitch: Annotated[float | None, Query(ge=-90, le=90)] = None,
    zoom: Annotated[float | None, Query(gt=0, le=16)] = None,
) -> Response:
    """Render a confirmed build's primary schematic and answer with the PNG.

    Every camera parameter is optional and defaults to the deployment's own framing, so a
    caller who wants "the picture of this build" gets the same recipe the durable queue
    renders, served from its stored artifact. Anything else is rendered on the spot, which
    takes as long as the engine takes.
    """
    await build_queries.get_public(build_id)
    rendered = await schematics.render_now(
        build_id,
        request=schematics.render_recipe(width=width, height=height, yaw=yaw, pitch=pitch, zoom=zoom),
    )
    return Response(
        content=rendered.png,
        media_type="image/png",
        headers={
            # The recipe hash covers the file, the pack, and the framing, so it changes
            # whenever the image would; the max-age stays short because the same URL starts
            # answering with a different image as soon as the build's primary attachment does.
            "ETag": f'"{rendered.recipe_hash}"',
            "Cache-Control": "public, max-age=300",
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
