"""Version 1 REST router assembly."""

from litestar import Router

from squid.api.rate_limit import enforce_route_rate_limits_before_request
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
from squid.api.v1.settings import router as settings_router
from squid.api.v1.submission_intake import router as submission_intake_router
from squid.api.v1.submission_media import router as submission_media_router
from squid.api.v1.submission_recalculations import router as submission_recalculations_router
from squid.api.v1.submission_schematics import router as submission_schematics_router
from squid.api.v1.submissions import router as submissions_router
from squid.api.v1.suggest import router as suggest_router
from squid.api.v1.tags import router as tags_router
from squid.api.v1.versions import router as versions_router
from squid.api.v1.votes import router as votes_router

router = Router(
    path="/v1",
    before_request=enforce_route_rate_limits_before_request,
    route_handlers=[
        auth_router,
        builds_router,
        capabilities_router,
        cli_auth_router,
        consent_router,
        diagnostics_router,
        me_router,
        me_accounts_router,
        minecraft_auth_router,
        notifications_router,
        records_router,
        schematics_router,
        search_router,
        settings_router,
        submissions_router,
        submission_media_router,
        submission_schematics_router,
        submission_recalculations_router,
        submission_intake_router,
        suggest_router,
        tags_router,
        creator_aliases_router,
        profiles_router,
        versions_router,
        votes_router,
    ],
)

TAGS_METADATA = [
    {"name": "settings", "description": "Global runtime configuration for application administrators."},
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
