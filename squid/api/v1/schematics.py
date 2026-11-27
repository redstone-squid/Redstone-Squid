"""Public schematic metadata and content routes."""

from typing import Annotated

from litestar import Request, Response, Router, get, post
from litestar.di import Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.params import FromPath, JSONBody, PathParameter, QueryParameter
from litestar.response import Redirect

from squid.api.contract import ANONYMOUS, DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import (
    BuildQueries,
    CurrentCaller,
    Schematics,
    get_build_queries,
    get_schematics,
)
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.links import relative_url_for
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.private_responses import private_response
from squid.api.v1.schemas.schematics import SchematicSummary, VideoCreateRequest, VideoResponse
from squid.core.actors import ActingContext
from squid.core.schematics.application.commands import MAX_RENDER_EXTENT, MIN_RENDER_EXTENT
from squid.core.schematics.errors import SchematicNotFoundError, SchematicSupportUnavailableError


@get(
    "/builds/{build_id:int}/schematics",
    responses=responses(
        400,
        404,
        422,
        503,
        describe={404: "No approved build with this identifier. A pending build answers 404 as well."},
    ),
    operation_id="build_schematics_list",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_build_schematics(
    build_id: FromPath[int],
    request: Request,
    build_queries: BuildQueries,
    schematics: Schematics,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[SchematicSummary]:
    """List analyzed schematics attached to a approved build."""
    await build_queries.get_public(build_id, context=ActingContext.current())
    # Attachment order has no identifier sequence worth anchoring to, so this listing is offset-only.
    selector = resolve_selector(offset=offset)
    page = await schematics.list_public_page(
        build_id, selector=selector, page_size=page_size, context=ActingContext.current()
    )
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


@get(
    "/builds/{build_id:int}/schematics/{schematic_id:int}/content",
    name="build_schematic_content",
    responses={
        **responses(404, 422, 503),
        200: ResponseSpec(
            generate_examples=False,
            data_container=bytes,
            media_type="application/octet-stream",
            description="Stored schematic content",
        ),
    },
    operation_id="build_schematic_content_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_schematic_content(
    build_id: FromPath[int],
    schematic_id: Annotated[int, PathParameter(ge=1)],
    build_queries: BuildQueries,
    schematics: Schematics,
) -> Response[bytes]:
    """Download explicitly published original bytes from a approved build."""
    await build_queries.get_public(build_id, context=ActingContext.current())
    download = await schematics.public_download(build_id, schematic_id, context=ActingContext.current())
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


@get(
    "/builds/{build_id:int}/schematics/render",
    responses={
        **responses(
            404,
            409,
            422,
            503,
            describe={
                404: "No approved build with this identifier, or it has no schematic attached.",
                409: "The attached schematic will never be previewed; `context.reason` says why.",
                503: "Previews are not configured on this instance, or the renderer is unavailable.",
            },
        ),
        200: ResponseSpec(
            generate_examples=False,
            data_container=bytes,
            media_type="image/png",
            description="Rendered schematic preview",
        ),
    },
    operation_id="build_schematic_render_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def render_build_schematic(
    build_id: FromPath[int],
    build_queries: BuildQueries,
    schematics: Schematics,
    width: Annotated[int | None, QueryParameter(ge=MIN_RENDER_EXTENT, le=MAX_RENDER_EXTENT)] = None,
    height: Annotated[int | None, QueryParameter(ge=MIN_RENDER_EXTENT, le=MAX_RENDER_EXTENT)] = None,
    yaw: Annotated[float | None, QueryParameter(ge=-360, le=360)] = None,
    pitch: Annotated[float | None, QueryParameter(ge=-90, le=90)] = None,
    zoom: Annotated[float | None, QueryParameter(gt=0, le=16)] = None,
) -> Response[bytes]:
    """Return a PNG preview of a approved build's primary schematic.

    Omitted camera parameters use deployment defaults. Reuse a stored preview
    when its recipe matches; otherwise render it during this request.
    """
    await build_queries.get_public(build_id, context=ActingContext.current())
    rendered = await schematics.render_now(
        build_id,
        request=schematics.render_recipe(width=width, height=height, yaw=yaw, pitch=pitch, zoom=zoom),
        context=ActingContext.current(),
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


@get(
    "/schematic-renders/{recipe_hash:str}/content",
    responses={
        **responses(404, 422, 503),
        200: ResponseSpec(
            generate_examples=False,
            data_container=bytes,
            media_type="image/png",
            description="Generated schematic preview",
        ),
    },
    operation_id="schematic_render_content_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_schematic_render_content(
    recipe_hash: Annotated[str, PathParameter(pattern=r"^[0-9a-f]{64}$")], schematics: Schematics
) -> Response[bytes]:
    """Return a content-addressed PNG preview from private object storage."""
    content = await schematics.render_content(recipe_hash, context=ActingContext.current())
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@get(
    "/schematic-resource-pack",
    status_code=307,
    responses=responses(503),
    operation_id="schematic_resource_pack_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_schematic_resource_pack(request: Request, schematics: Schematics) -> Redirect:
    """Redirect to the content-addressed resource pack that server renders use."""
    pack = await schematics.previews.resource_pack(context=ActingContext.current())
    return Redirect(
        relative_url_for(request, "schematic_resource_pack_content", sha256=pack.sha256),
        status_code=307,
        # Short, because the target changes as soon as an operator configures another pack.
        headers={"Cache-Control": "public, max-age=300"},
    )


@get(
    "/schematic-resource-packs/{sha256:str}",
    name="schematic_resource_pack_content",
    responses={
        **responses(404, 422, 503),
        200: ResponseSpec(
            generate_examples=False,
            data_container=bytes,
            media_type="application/zip",
            description="Minecraft resource pack",
        ),
    },
    operation_id="schematic_resource_pack_content_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_schematic_resource_pack_content(
    sha256: Annotated[str, PathParameter(pattern=r"^[0-9a-f]{64}$")], schematics: Schematics
) -> Response[bytes]:
    """Return the configured resource pack while its digest still matches."""
    pack = await schematics.previews.resource_pack(context=ActingContext.current())
    if pack.sha256 != sha256:
        raise SchematicNotFoundError(context={"sha256": sha256})
    return Response(
        content=pack.data,
        media_type=pack.media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@post(
    "/builds/{build_id:int}/schematics/{schematic_id:int}/videos",
    status_code=202,
    operation_id="schematic_video_create",
    responses=responses(403, 404, 409, 413, 422, 503),
    before_request=reserve_idempotent_before_request,
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
)
async def submit_schematic_video(
    build_id: FromPath[int],
    schematic_id: FromPath[int],
    data: JSONBody[VideoCreateRequest],
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
    request: Request,
) -> Response[VideoResponse]:
    """Queue a private MP4 of a stored schematic with timed input activations."""
    caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    await build_queries.get_public(build_id, context=caller.context())
    status = await schematics.videos.submit_video(
        build_id,
        schematic_id,
        data.to_request(),
        context=caller.context(),
    )
    await build_queries.get_public(status.build_id, context=caller.context())
    result = VideoResponse.from_status(
        status,
        status_url=relative_url_for(request, "get_schematic_video", video_id=status.id),
        download_url=relative_url_for(request, "get_schematic_video_content", video_id=status.id),
    )
    return Response(result, status_code=202, headers={"Cache-Control": "private, no-store"})


@get(
    "/accounts/me/schematic-videos",
    operation_id="account_schematic_video_list",
    responses=responses(400, 401, 422, 503),
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
)
@private_response
async def list_own_schematic_videos(
    schematics: Schematics,
    caller: CurrentCaller,
    request: Request,
    schematic_id: Annotated[int | None, QueryParameter(ge=1)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
) -> Page[VideoResponse]:
    """Page the videos the caller requested, newest first, optionally for one schematic.

    Opening a video still checks that its schematic is published.
    """
    caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    page = await schematics.videos.list_videos(
        schematic_id=schematic_id,
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
        context=caller.context(),
    )
    return Page[VideoResponse].from_domain(
        page,
        lambda status: VideoResponse.from_status(
            status,
            status_url=relative_url_for(request, "get_schematic_video", video_id=status.id),
            download_url=relative_url_for(request, "get_schematic_video_content", video_id=status.id),
        ),
    )


@get(
    "/schematic-videos/{video_id:int}",
    operation_id="schematic_video_status",
    name="get_schematic_video",
    responses=responses(403, 404, 422, 503),
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
)
async def get_schematic_video(
    video_id: FromPath[int],
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
    request: Request,
) -> Response[VideoResponse]:
    """Return only the requesting account's video status."""
    caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    status = await schematics.videos.get_video(
        video_id,
        context=caller.context(),
    )
    await build_queries.get_public(status.build_id, context=caller.context())
    result = VideoResponse.from_status(
        status,
        status_url=relative_url_for(request, "get_schematic_video", video_id=status.id),
        download_url=relative_url_for(request, "get_schematic_video_content", video_id=status.id),
    )
    return Response(result, headers={"Cache-Control": "private, no-store"})


@get(
    "/schematic-videos/{video_id:int}/content",
    operation_id="schematic_video_content",
    name="get_schematic_video_content",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(cli=transport_only()),
    responses={
        **responses(403, 404, 409, 422, 503),
        200: ResponseSpec(
            generate_examples=False, data_container=bytes, media_type="video/mp4", description="Private schematic video"
        ),
    },
)
async def get_schematic_video_content(
    video_id: FromPath[int],
    build_queries: BuildQueries,
    schematics: Schematics,
    caller: CurrentCaller,
) -> Response[bytes]:
    """Download a completed private video while its source remains accessible."""
    caller.require_consented_account()
    if schematics.videos is None:
        raise SchematicSupportUnavailableError
    status = await schematics.videos.get_video(
        video_id,
        context=caller.context(),
    )
    await build_queries.get_public(status.build_id, context=caller.context())
    content = await schematics.videos.open_video_content(
        video_id,
        context=caller.context(),
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


router = Router(
    dependencies={
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
        "schematics": Provide(get_schematics, sync_to_thread=False),
    },
    path="/",
    tags=["schematics"],
    route_handlers=[
        list_build_schematics,
        get_schematic_content,
        render_build_schematic,
        get_schematic_render_content,
        get_schematic_resource_pack,
        get_schematic_resource_pack_content,
        submit_schematic_video,
        list_own_schematic_videos,
        get_schematic_video,
        get_schematic_video_content,
    ],
)
