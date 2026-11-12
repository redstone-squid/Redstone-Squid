"""Public schematic metadata and content routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from squid.api.contract import ANONYMOUS, DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import BuildQueries, CurrentCaller, Permissions, Schematics
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.links import relative_url_for
from squid.api.pagination import OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.v1.schemas.schematics import SchematicSummary, VideoCreateRequest, VideoResponse
from squid.permissions.domain.catalogue import BUILD_SCHEMATIC_VIEW_PRIVATE
from squid.schematics.application.commands import MAX_RENDER_EXTENT, MIN_RENDER_EXTENT
from squid.schematics.errors import SchematicSupportUnavailableError

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
    operation_id="build_schematics_list",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_build_schematics(
    build_id: int,
    request: Request,
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
    return Page[SchematicSummary].from_domain(
        page,
        lambda schematic: SchematicSummary.from_domain(
            schematic,
            download_url=relative_url_for(
                request,
                "build_schematic_content",
                build_id=schematic.build_id,
                schematic_id=schematic.id,
            ),
        ),
    )


@router.get(
    "/builds/{build_id}/schematics/{schematic_id}/content",
    name="build_schematic_content",
    response_class=Response,
    responses={
        **responses(404, 422, 503),
        200: {"content": {"application/octet-stream": {}}, "description": "Stored schematic content"},
    },
    operation_id="build_schematic_content_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_schematic_content(
    build_id: int,
    schematic_id: Annotated[int, Path(ge=1)],
    build_queries: BuildQueries,
    schematics: Schematics,
) -> Response:
    """Download explicitly published original bytes from a confirmed build."""
    await build_queries.get_public(build_id)
    download = await schematics.public_download(build_id, schematic_id)
    # Generate the filename from IDs and the known format so user-supplied names
    # cannot introduce characters into the response header.
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
    operation_id="build_schematic_render_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
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
    """Return a PNG preview of a confirmed build's primary schematic.

    Omitted camera parameters use deployment defaults. Reuse a stored preview
    when its recipe matches; otherwise render it during this request.
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
    operation_id="schematic_render_content_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
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


@router.post(
    "/builds/{build_id}/schematics/{schematic_id}/videos",
    status_code=202,
    response_model=VideoResponse,
    operation_id="schematic_video_create",
    responses=responses(403, 404, 409, 413, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
    openapi_extra=contract(security=[WEB_WRITE, DEVICE, MINECRAFT], cli=transport_only()),
)
async def submit_schematic_video(
    build_id: int,
    schematic_id: int,
    body: VideoCreateRequest,
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
    permissions: Permissions,
    request: Request,
    response: Response,
) -> VideoResponse:
    """Queue a private MP4 of a stored schematic with timed input activations."""
    account_id = caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    await build_queries.get_public(build_id)
    status = await schematics.videos.submit_video(
        build_id,
        schematic_id,
        body.to_request(),
        account_id=account_id,
        can_view_private=await caller.allows(permissions, BUILD_SCHEMATIC_VIEW_PRIVATE),
    )
    await build_queries.get_public(status.build_id)
    response.headers["Cache-Control"] = "private, no-store"
    return VideoResponse.from_status(
        status,
        status_url=relative_url_for(request, "get_schematic_video", video_id=status.id),
        download_url=relative_url_for(request, "get_schematic_video_content", video_id=status.id),
    )


@router.get(
    "/schematic-videos/{video_id}",
    response_model=VideoResponse,
    operation_id="schematic_video_status",
    responses=responses(403, 404, 422, 503),
    openapi_extra=contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only()),
)
async def get_schematic_video(
    video_id: int,
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
    permissions: Permissions,
    request: Request,
    response: Response,
) -> VideoResponse:
    """Return only the requesting account's video status."""
    account_id = caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    status = await schematics.videos.get_video(
        video_id,
        account_id=account_id,
        can_view_private=await caller.allows(permissions, BUILD_SCHEMATIC_VIEW_PRIVATE),
    )
    await build_queries.get_public(status.build_id)
    response.headers["Cache-Control"] = "private, no-store"
    return VideoResponse.from_status(
        status,
        status_url=relative_url_for(request, "get_schematic_video", video_id=status.id),
        download_url=relative_url_for(request, "get_schematic_video_content", video_id=status.id),
    )


@router.get(
    "/schematic-videos/{video_id}/content",
    response_class=Response,
    operation_id="schematic_video_content",
    openapi_extra=contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only()),
    responses={
        **responses(403, 404, 409, 422, 503),
        200: {"content": {"video/mp4": {}}, "description": "Private schematic video"},
    },
)
async def get_schematic_video_content(
    video_id: int,
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
    permissions: Permissions,
) -> Response:
    """Download a completed private video while its source remains accessible."""
    account_id = caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    status = await schematics.videos.get_video(
        video_id,
        account_id=account_id,
        can_view_private=await caller.allows(permissions, BUILD_SCHEMATIC_VIEW_PRIVATE),
    )
    await build_queries.get_public(status.build_id)
    content = await schematics.videos.open_video_content(
        video_id,
        account_id=account_id,
        can_view_private=await caller.allows(permissions, BUILD_SCHEMATIC_VIEW_PRIVATE),
    )
    return Response(
        content,
        media_type="video/mp4",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="schematic-video-{video_id}.mp4"',
        },
    )
