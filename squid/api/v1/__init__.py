"""Version 1 REST router assembly."""

from fastapi import APIRouter, Depends

from squid.api.errors import responses
from squid.api.rate_limit import enforce_route_rate_limits
from squid.api.v1.auth import router as auth_router
from squid.api.v1.builds import router as builds_router
from squid.api.v1.capabilities import router as capabilities_router
from squid.api.v1.cli_auth import router as cli_auth_router
from squid.api.v1.consent import router as consent_router
from squid.api.v1.creators import profiles_router
from squid.api.v1.creators import router as creator_aliases_router
from squid.api.v1.diagnostics import router as diagnostics_router
from squid.api.v1.me import accounts_router as me_accounts_router
from squid.api.v1.me import router as me_router
from squid.api.v1.minecraft_auth import router as minecraft_auth_router
from squid.api.v1.notifications import router as notifications_router
from squid.api.v1.records import router as records_router
from squid.api.v1.schematics import router as schematics_router
from squid.api.v1.search import router as search_router
from squid.api.v1.submission_media import router as submission_media_router
from squid.api.v1.submissions import router as submissions_router
from squid.api.v1.suggest import router as suggest_router
from squid.api.v1.tags import router as tags_router
from squid.api.v1.versions import router as versions_router
from squid.api.v1.votes import router as votes_router

router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(enforce_route_rate_limits)],
    responses=responses(429),
)
router.include_router(auth_router)
router.include_router(builds_router)
router.include_router(capabilities_router)
router.include_router(cli_auth_router)
router.include_router(consent_router)
router.include_router(diagnostics_router)
router.include_router(me_router)
router.include_router(me_accounts_router)
router.include_router(minecraft_auth_router)
router.include_router(notifications_router)
router.include_router(records_router)
router.include_router(schematics_router)
router.include_router(search_router)
router.include_router(submissions_router)
router.include_router(submission_media_router)
router.include_router(suggest_router)
router.include_router(tags_router)
router.include_router(creator_aliases_router)
router.include_router(profiles_router)
router.include_router(versions_router)
router.include_router(votes_router)

TAGS_METADATA = [
    {"name": "capabilities", "description": "API, protocol, renderer, and upload compatibility."},
    {"name": "authentication", "description": "Discord OAuth2 browser sessions."},
    {"name": "cli-authentication", "description": "Browser-approved CLI devices and short-lived sessions."},
    {"name": "users", "description": "Authenticated self-service account operations."},
    {"name": "notifications", "description": "Notification preferences, subscriptions, and inbox."},
    {
        "name": "minecraft-authentication",
        "description": "Paper installation credentials and player-bound device authorization.",
    },
    {"name": "builds", "description": "Public redstone build catalog."},
    {"name": "search", "description": "Search grammar and field discovery."},
    {"name": "suggest", "description": "Typeahead completions for registered value sources."},
    {"name": "submissions", "description": "Revisioned submission forms and drafts."},
    {"name": "records", "description": "Active computed record results."},
    {"name": "tags", "description": "Published build and record taxonomy."},
    {"name": "versions", "description": "Recognized Minecraft versions."},
    {"name": "creators", "description": "Public creator profiles and credits."},
    {"name": "schematics", "description": "Schematic analysis metadata and content."},
    {"name": "vote sessions", "description": "Aggregate ballot-safe vote state."},
    {"name": "diagnostics", "description": "Stored error reports, resolvable by the reference a user was shown."},
]
