"""FastAPI dependencies shared by versioned routes."""

from typing import Annotated, cast

from fastapi import Depends, Request

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


type Services = Annotated[ApiServices, Depends(get_services)]


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
    """Return the attachment boundary only when this API enables media jobs."""
    attachments = draft_attachment_service(services.submission_drafts, services.media_jobs)
    if attachments is None:
        raise DraftMediaUnavailableError
    return attachments


type Permissions = Annotated[PermissionService, Depends(get_permissions)]
type ErrorReports = Annotated[ErrorReportService, Depends(get_error_reports)]
type DraftAttachments = Annotated[DraftAttachmentService, Depends(get_draft_attachments)]
type BuildCommands = Annotated[BuildService, Depends(get_builds)]
type BuildQueries = Annotated[BuildQueryService, Depends(get_build_queries)]
type CurrentCaller = Annotated[Caller, Depends(current_caller)]
type Records = Annotated[RecordService, Depends(get_records)]
type PublicRecords = Annotated[PublicRecordQueryService, Depends(get_public_records)]
type Notifications = Annotated[NotificationService, Depends(get_notifications)]
type Schematics = Annotated[SchematicService, Depends(get_schematics)]
type Search = Annotated[SearchService, Depends(get_search)]
type Suggestions = Annotated[SuggestionService, Depends(get_suggestions)]
type Tags = Annotated[TagService, Depends(get_tags)]
type Accounts = Annotated[AccountService, Depends(get_accounts)]
type Versions = Annotated[VersionService, Depends(get_versions)]
type VoteMembers = Annotated[InteractiveVoteActorResolver | None, Depends(get_vote_members)]
type Votes = Annotated[VoteService, Depends(get_votes)]
type WebAuth = Annotated[WebSessionService | None, Depends(get_web_auth)]
type MinecraftInstallations = Annotated[InstallationCredentialService, Depends(get_minecraft_installations)]
type MinecraftPlayerAuthorization = Annotated[
    PlayerAuthorizationService,
    Depends(get_minecraft_player_authorization),
]
