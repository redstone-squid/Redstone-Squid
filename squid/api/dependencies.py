"""FastAPI dependencies shared by versioned routes."""

from typing import Annotated, cast

from fastapi import Depends, Query, Request

from squid.accounts.application import AccountService
from squid.api.security import Principal, current_principal
from squid.auth.application.web import DiscordOAuthService
from squid.builds.application import BuildQueryService, BuildService
from squid.core.pagination import SignedCursor
from squid.notifications import NotificationService
from squid.permissions.application import PermissionService
from squid.records.application import RecordService
from squid.runtime import ApiServices, ApplicationRuntime
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.tags.application import TagService
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.application.ports import InteractiveVoteActorResolver


async def get_services(request: Request) -> ApiServices:
    """Return application services initialized during API startup."""
    runtime = cast(ApplicationRuntime[ApiServices], request.app.state.runtime)
    return runtime.services


Services = Annotated[ApiServices, Depends(get_services)]


def get_builds(services: Services) -> BuildService:
    return services.builds


def get_build_queries(services: Services) -> BuildQueryService:
    return services.build_queries


def get_records(services: Services) -> RecordService:
    return services.records


def get_notifications(services: Services) -> NotificationService:
    return services.notifications


def get_schematics(services: Services) -> SchematicService:
    return services.schematics


def get_search(services: Services) -> SearchService:
    return services.search


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


def get_web_auth(services: Services) -> DiscordOAuthService | None:
    return services.web_auth


def get_permissions(services: Services) -> PermissionService:
    return services.permissions


async def cursor_signer(request: Request) -> SignedCursor:
    """Return a collection cursor signer using shared runtime configuration."""
    config = request.app.state.config
    return SignedCursor(config.runtime.cursor_secret.get_secret_value().encode())


PageSize = Annotated[int, Query(ge=1, le=50)]
Permissions = Annotated[PermissionService, Depends(get_permissions)]
BuildCommands = Annotated[BuildService, Depends(get_builds)]
BuildQueries = Annotated[BuildQueryService, Depends(get_build_queries)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
CursorSigner = Annotated[SignedCursor, Depends(cursor_signer)]
Records = Annotated[RecordService, Depends(get_records)]
Notifications = Annotated[NotificationService, Depends(get_notifications)]
Schematics = Annotated[SchematicService, Depends(get_schematics)]
Search = Annotated[SearchService, Depends(get_search)]
Tags = Annotated[TagService, Depends(get_tags)]
Accounts = Annotated[AccountService, Depends(get_accounts)]
Versions = Annotated[VersionService, Depends(get_versions)]
VoteMembers = Annotated[InteractiveVoteActorResolver | None, Depends(get_vote_members)]
Votes = Annotated[VoteService, Depends(get_votes)]
WebAuth = Annotated[DiscordOAuthService | None, Depends(get_web_auth)]
