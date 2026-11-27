"""Version 1 REST router assembly."""

from litestar import Router

from squid.api.rate_limit import enforce_route_rate_limits_before_request
from squid.api.v1.accounts import router as accounts_router
from squid.api.v1.auth import router as auth_router
from squid.api.v1.builds import router as builds_router
from squid.api.v1.capabilities import router as capabilities_router
from squid.api.v1.cli_auth import router as cli_auth_router
from squid.api.v1.consent import router as consent_router
from squid.api.v1.creators import router as creator_aliases_router
from squid.api.v1.diagnostics import router as diagnostics_router
from squid.api.v1.minecraft_auth import router as minecraft_auth_router
from squid.api.v1.notifications import router as notifications_router
from squid.api.v1.permissions import router as permissions_router
from squid.api.v1.records import router as records_router
from squid.api.v1.schematics import router as schematics_router
from squid.api.v1.search import router as search_router
from squid.api.v1.session import inspect_impersonation, session_snapshot, start_impersonation, stop_impersonation
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
        accounts_router,
        auth_router,
        builds_router,
        capabilities_router,
        cli_auth_router,
        consent_router,
        diagnostics_router,
        minecraft_auth_router,
        notifications_router,
        permissions_router,
        records_router,
        schematics_router,
        search_router,
        settings_router,
        session_snapshot,
        start_impersonation,
        inspect_impersonation,
        stop_impersonation,
        submissions_router,
        submission_media_router,
        submission_schematics_router,
        submission_recalculations_router,
        submission_intake_router,
        suggest_router,
        tags_router,
        creator_aliases_router,
        versions_router,
        votes_router,
    ],
)

TAGS_METADATA = [
    {"name": "settings", "description": "Global runtime configuration for application administrators."},
    {"name": "capabilities", "description": "API, protocol, renderer, and upload compatibility."},
    {"name": "authentication", "description": "Discord OAuth2 browser sessions."},
    {"name": "cli-authentication", "description": "Browser-approved CLI devices and short-lived sessions."},
    {"name": "accounts", "description": "Your own account, public account profiles, and staff moderation."},
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
    {"name": "tags", "description": "Published build and record tags."},
    {"name": "versions", "description": "Recognized Minecraft versions and their corrections."},
    {"name": "creators", "description": "Creator names credited on builds."},
    {"name": "schematics", "description": "Schematic analysis metadata and content."},
    {"name": "vote sessions", "description": "Aggregate ballot-safe vote state."},
    {"name": "diagnostics", "description": "Stored error reports, resolvable by the reference a user was shown."},
    {"name": "permissions", "description": "Permission grants, roles, assignments, explanations, and their audit log."},
]
