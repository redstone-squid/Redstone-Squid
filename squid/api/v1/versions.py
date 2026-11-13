"""Public Minecraft-version read routes."""

from litestar import Router, get

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Versions
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam
from squid.api.v1.schemas.versions import VersionDetail
from squid.core.pagination import offset_page


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="minecraft_versions_list",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_versions(
    versions_service: Versions,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[VersionDetail]:
    """List recognized Java and Bedrock releases."""
    versions = await versions_service.list_all()
    page = offset_page(versions, offset=offset or 0, page_size=page_size)
    return Page[VersionDetail].from_domain(page, VersionDetail.from_domain)


router = Router(path="/versions", tags=["versions"], route_handlers=[list_versions])
