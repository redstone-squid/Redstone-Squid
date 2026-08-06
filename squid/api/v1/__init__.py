"""Version 1 REST router assembly."""

from fastapi import APIRouter

from squid.api.v1.builds import router as builds_router
from squid.api.v1.records import router as records_router
from squid.api.v1.schematics import router as schematics_router
from squid.api.v1.search import router as search_router
from squid.api.v1.tags import router as tags_router
from squid.api.v1.users import router as users_router
from squid.api.v1.versions import router as versions_router
from squid.api.v1.votes import router as votes_router

router = APIRouter(prefix="/v1")
router.include_router(builds_router)
router.include_router(records_router)
router.include_router(schematics_router)
router.include_router(search_router)
router.include_router(tags_router)
router.include_router(users_router)
router.include_router(versions_router)
router.include_router(votes_router)

TAGS_METADATA = [
    {"name": "builds", "description": "Public redstone build catalog."},
    {"name": "search", "description": "Search grammar and field discovery."},
    {"name": "records", "description": "Active computed record results."},
    {"name": "tags", "description": "Published build and record taxonomy."},
    {"name": "versions", "description": "Recognized Minecraft versions."},
    {"name": "creator aliases", "description": "Public creator credits."},
    {"name": "schematics", "description": "Schematic analysis metadata and content."},
    {"name": "vote sessions", "description": "Aggregate ballot-safe vote state."},
]
