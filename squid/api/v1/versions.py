"""Minecraft-version catalogue routes."""

from litestar import Router, delete, get, patch, post
from litestar.di import Provide
from litestar.params import FromPath, FromQuery, JSONBody

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB_WRITE, contract, transport_only
from squid.api.dependencies import CurrentCaller, Versions, get_versions
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.pagination import CursorParam, ListResponse, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.security import requires
from squid.api.v1.schemas.versions import VersionCorrectionRequest, VersionCreateRequest, VersionDetail
from squid.core.actors import ActingContext
from squid.core.permissions.domain.catalogue import VERSION_ENTRY_CREATE, VERSION_ENTRY_DELETE, VERSION_ENTRY_EDIT
from squid.core.versions.domain.models import VersionListSort


@get(
    "/",
    responses=responses(400, 422, 503),
    response_class=ListResponse,
    operation_id="minecraft_versions_list",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_versions(
    versions_service: Versions,
    page_size: PageSizeParam = 50,
    sort: FromQuery[VersionListSort] = VersionListSort.RELEASE,
    cursor: CursorParam = None,
    offset: OffsetParam = None,
) -> Page[VersionDetail]:
    """List recognized Java and Bedrock releases."""
    versions = await versions_service.list_all(context=ActingContext.current())
    page = sort.page(versions, selector=resolve_selector(offset=offset, cursor=cursor), page_size=page_size)
    return Page[VersionDetail].from_domain(page, VersionDetail.from_domain)


@post(
    "/",
    status_code=201,
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="minecraft_versions_create",
    before_request=reserve_idempotent_before_request,
    guards=[requires(VERSION_ENTRY_CREATE)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("version.entry.create",)),
)
async def create_version(
    data: JSONBody[VersionCreateRequest], versions_service: Versions, caller: CurrentCaller
) -> VersionDetail:
    """Add a release the announcement channel missed."""
    entry = await versions_service.add(data.version, edition=data.edition, context=caller.context())
    return VersionDetail.from_domain(entry)


@patch(
    "/{version_id:int}",
    status_code=200,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="minecraft_versions_correct",
    before_request=reserve_idempotent_before_request,
    guards=[requires(VERSION_ENTRY_EDIT)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("version.entry.edit",)),
)
async def correct_version(
    version_id: FromPath[int],
    data: JSONBody[VersionCorrectionRequest],
    versions_service: Versions,
    caller: CurrentCaller,
) -> VersionDetail:
    """Fix a mistaken release in place. Builds and records that use it follow the correction."""
    entry = await versions_service.correct(version_id, data.to_domain(), context=caller.context())
    return VersionDetail.from_domain(entry)


@delete(
    "/{version_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="minecraft_versions_delete",
    before_request=reserve_idempotent_before_request,
    guards=[requires(VERSION_ENTRY_DELETE)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("version.entry.delete",)),
)
async def delete_version(version_id: FromPath[int], versions_service: Versions, caller: CurrentCaller) -> None:
    """Delete a release nothing uses yet. A release that builds or records use answers 409; correct it instead."""
    await versions_service.delete(version_id, context=caller.context())


router = Router(
    dependencies={"versions_service": Provide(get_versions, sync_to_thread=False)},
    path="/versions",
    tags=["versions"],
    route_handlers=[list_versions, create_version, correct_version, delete_version],
)
