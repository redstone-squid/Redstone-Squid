"""Search discovery routes."""

from fastapi import APIRouter

from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.v1.schemas.search import SearchField

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/fields", response_model=list[SearchField], responses=responses(503))
async def list_search_fields(services: Services) -> list[SearchField]:
    """Publish the effective allowlisted query-field registry."""
    registry = await services.search.fields()
    return [SearchField.from_domain(field) for field in registry.definitions]
