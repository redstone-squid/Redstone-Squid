"""Version 1 REST router assembly."""

from fastapi import APIRouter

from squid.api.v1.builds import router as builds_router
from squid.api.v1.search import router as search_router

router = APIRouter(prefix="/v1")
router.include_router(builds_router)
router.include_router(search_router)

TAGS_METADATA = [
    {"name": "builds", "description": "Public redstone build catalog."},
    {"name": "search", "description": "Search grammar and field discovery."},
]
