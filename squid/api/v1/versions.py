"""Public Minecraft-version read routes."""

from fastapi import APIRouter

from squid.api.dependencies import Versions
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page
from squid.api.v1.schemas.versions import VersionDetail
from squid.core.pagination import offset_page

router = APIRouter(prefix="/versions", tags=["versions"])


@router.get("", response_model=Page[VersionDetail], responses=responses(400, 422, 503))
async def list_versions(
    versions_service: Versions,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[VersionDetail]:
    """List recognized Java and Bedrock releases."""
    versions = await versions_service.list_all()
    page = offset_page(versions, offset=offset or 0, page_size=page_size)
    return render_page(page, VersionDetail.from_domain)
