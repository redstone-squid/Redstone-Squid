"""Named Litestar dependencies shared by versioned routes."""

from typing import cast

from litestar import Request
from litestar.di import NamedDependency, Provide
from litestar.params import SkipValidation

from squid.accounts.application import AccountService
from squid.api.security import Caller, current_caller
from squid.auth.application.web import WebSessionService
from squid.builds.application import BuildQueryService, BuildService
from squid.core.errors import ServiceUnavailableError
from squid.diagnostics.application import ErrorReportService
from squid.media.errors import DraftMediaUnavailableError
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.notifications import NotificationService
from squid.permissions.application import PermissionService
from squid.records.application import PublicRecordQueryService, RecordService
from squid.runtime import ApiServices, ApplicationRuntime
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.submissions.application import DraftAttachmentService, draft_attachment_service
from squid.suggestions.application import SuggestionService
from squid.tags.application import TagService
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.application.ports import InteractiveVoteActorResolver


async def get_services(request: Request) -> ApiServices:
    """Return application services initialized during API startup."""
    runtime = cast(ApplicationRuntime[ApiServices], request.app.state.runtime)
    return runtime.services


Services = NamedDependency[SkipValidation[ApiServices]]


def get_builds(services: Services) -> BuildService:
    return services.builds


def get_build_queries(services: Services) -> BuildQueryService:
    return services.build_queries


def get_records(services: Services) -> RecordService:
    return services.records


def get_public_records(services: Services) -> PublicRecordQueryService:
    return services.public_records


def get_notifications(services: Services) -> NotificationService:
    return services.notifications


def get_schematics(services: Services) -> SchematicService:
    return services.schematics


def get_search(services: Services) -> SearchService:
    return services.search


def get_suggestions(services: Services) -> SuggestionService:
    return services.suggestions


def get_tags(services: Services) -> TagService:
    return services.tags


def get_accounts(services: Services) -> AccountService:
    return services.accounts


def get_versions(services: Services) -> VersionService:
    return services.versions


def get_votes(services: Services) -> VoteService:
    return services.votes


def get_vote_members(services: Services) -> InteractiveVoteActorResolver | None:
    return services.vote_members


def get_web_auth(services: Services) -> WebSessionService | None:
    return services.web_auth


def get_permissions(services: Services) -> PermissionService:
    return services.permissions


def get_error_reports(services: Services) -> ErrorReportService:
    return services.error_reports


def get_minecraft_installations(services: Services) -> InstallationCredentialService:
    service = services.minecraft_installations
    if service is None:
        raise ServiceUnavailableError(resource="minecraft_auth")
    return service


def get_minecraft_player_authorization(services: Services) -> PlayerAuthorizationService:
    service = services.minecraft_player_authorization
    if service is None:
        raise ServiceUnavailableError(resource="minecraft_auth")
    return service


async def get_draft_attachments(services: Services) -> DraftAttachmentService:
    """Return draft attachment services, or raise DraftMediaUnavailableError if media jobs are disabled."""
    attachments = draft_attachment_service(services.submission_drafts, services.media_jobs)
    if attachments is None:
        raise DraftMediaUnavailableError
    return attachments


Permissions = NamedDependency[SkipValidation[PermissionService]]
ErrorReports = NamedDependency[SkipValidation[ErrorReportService]]
DraftAttachments = NamedDependency[SkipValidation[DraftAttachmentService]]
BuildCommands = NamedDependency[SkipValidation[BuildService]]
BuildQueries = NamedDependency[SkipValidation[BuildQueryService]]
CurrentCaller = NamedDependency[SkipValidation[Caller]]
Records = NamedDependency[SkipValidation[RecordService]]
PublicRecords = NamedDependency[SkipValidation[PublicRecordQueryService]]
Notifications = NamedDependency[SkipValidation[NotificationService]]
Schematics = NamedDependency[SkipValidation[SchematicService]]
Search = NamedDependency[SkipValidation[SearchService]]
Suggestions = NamedDependency[SkipValidation[SuggestionService]]
Tags = NamedDependency[SkipValidation[TagService]]
Accounts = NamedDependency[SkipValidation[AccountService]]
Versions = NamedDependency[SkipValidation[VersionService]]
VoteMembers = NamedDependency[SkipValidation[InteractiveVoteActorResolver | None]]
Votes = NamedDependency[SkipValidation[VoteService]]
WebAuth = NamedDependency[SkipValidation[WebSessionService | None]]
MinecraftInstallations = NamedDependency[SkipValidation[InstallationCredentialService]]
MinecraftPlayerAuthorization = NamedDependency[SkipValidation[PlayerAuthorizationService]]


def get_caller(current_identity: NamedDependency[SkipValidation[Caller]]) -> Caller:
    return current_identity


def get_search_service(services: Services) -> SearchService:
    return services.search


def get_versions_service(services: Services) -> VersionService:
    return services.versions


def get_players(services: Services) -> PlayerAuthorizationService:
    return get_minecraft_player_authorization(services)


def service_dependencies() -> dict[str, Provide]:
    """Bind application services by the argument names used in HTTP handlers."""
    return {
        "services": Provide(get_services),
        "players": Provide(get_players, sync_to_thread=False),
        "installations": Provide(get_minecraft_installations, sync_to_thread=False),
        "authorization": Provide(get_minecraft_player_authorization, sync_to_thread=False),
        "current_identity": Provide(current_caller),
        "caller": Provide(get_caller, sync_to_thread=False),
        "builds": Provide(get_builds, sync_to_thread=False),
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
        "records": Provide(get_records, sync_to_thread=False),
        "notifications": Provide(get_notifications, sync_to_thread=False),
        "schematics": Provide(get_schematics, sync_to_thread=False),
        "search": Provide(get_search, sync_to_thread=False),
        "search_service": Provide(get_search_service, sync_to_thread=False),
        "suggestions": Provide(get_suggestions, sync_to_thread=False),
        "tags": Provide(get_tags, sync_to_thread=False),
        "accounts": Provide(get_accounts, sync_to_thread=False),
        "versions": Provide(get_versions, sync_to_thread=False),
        "versions_service": Provide(get_versions_service, sync_to_thread=False),
        "votes": Provide(get_votes, sync_to_thread=False),
        "vote_members": Provide(get_vote_members, sync_to_thread=False),
        "web_auth": Provide(get_web_auth, sync_to_thread=False),
        "permissions": Provide(get_permissions, sync_to_thread=False),
        "error_reports": Provide(get_error_reports, sync_to_thread=False),
        "attachments": Provide(get_draft_attachments),
    }
