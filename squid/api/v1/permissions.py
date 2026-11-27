"""Permission grants, roles, assignments, explanations and the audit log.

The website acts without a guild, so only global administrators can change
permissions here; core refuses guild delegates, who use `/access` in their server.
`guild_id` filters lists, and on writes it names the guild a rule applies in.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from litestar import Router, delete, get, patch, post
from litestar.di import Provide
from litestar.params import FromPath, JSONBody, QueryParameter
from pydantic import BaseModel, ConfigDict
from whenever import Instant

from squid.api.contract import DEVICE, SERVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import CurrentCaller, PermissionAdmin, Services, get_permission_admin
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.security import Caller, requires
from squid.api.v1.schemas.permissions import (
    SNOWFLAKE_PATTERN,
    AccountSubjectRequest,
    AssignmentDetail,
    AssignmentRequest,
    AuditEntryDetail,
    DecisionDetail,
    GrantDetail,
    GrantRequest,
    PermissionDomainDetail,
    RoleCreateRequest,
    RoleDetail,
    RolePatternRequest,
    RoleRankRequest,
    SubjectRequest,
)
from squid.core.errors import ValidationError
from squid.core.pagination import offset_page
from squid.core.permissions.domain import CATALOGUE, Effect
from squid.core.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid.core.permissions.domain.models import (
    AccountGrantSubject,
    ExternalRoleGrantSubject,
    GrantSubject,
    PatternMode,
)
from squid.core.validation import FieldErrorCode, SimpleIssue

_READ = [WEB, DEVICE, SERVICE]
_WRITE = [WEB_WRITE, DEVICE, SERVICE]

AccountQuery = Annotated[UUID | None, QueryParameter(description="Only rows about this account.")]
DiscordRoleQuery = Annotated[
    str | None, QueryParameter(pattern=SNOWFLAKE_PATTERN, description="Only rows about this Discord role.")
]
GuildQuery = Annotated[
    str | None,
    QueryParameter(pattern=SNOWFLAKE_PATTERN, description="Only rows about this guild, or resolve in it."),
]
_EFFECTS = {"allow": Effect.ALLOW, "deny": Effect.DENY, "forbid": Effect.FORBID}


class IncludedRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: int


class SubjectQuery:
    """The subject named by `account_id`, or by `guild_id` with `discord_role_id`."""

    def __init__(self, account: int | None, discord_role_id: int | None, guild_id: int | None) -> None:
        self.account_id = account
        self.discord_role_id = discord_role_id
        self.guild_id = guild_id

    @classmethod
    async def read(
        cls,
        services: Services,
        caller: Caller,
        account_id: UUID | None,
        discord_role_id: str | None,
        guild_id: str | None,
    ) -> Self:
        if account_id is not None and discord_role_id is not None:
            msg = "account_id and discord_role_id cannot be combined"
            raise ValidationError(
                msg,
                issues=tuple(
                    SimpleIssue(field=f"/query/{key}", code=FieldErrorCode.INCOMPATIBLE_FIELDS)
                    for key in ("account_id", "discord_role_id")
                ),
            )
        account = (
            None
            if account_id is None
            else await services.accounts.resolve_public_id(account_id, context=caller.context())
        )
        return cls(account, _int(discord_role_id), _int(guild_id))

    def subject(self) -> GrantSubject:
        if self.account_id is not None:
            return AccountGrantSubject(self.account_id)
        if self.discord_role_id is None or self.guild_id is None:
            msg = "Name an account, or a Discord role with its guild."
            raise ValidationError(msg, issues=(SimpleIssue(field="/query/account_id", code=FieldErrorCode.REQUIRED),))
        return ExternalRoleGrantSubject(guild_id=self.guild_id, discord_role_id=self.discord_role_id)


def _int(value: str | None) -> int | None:
    return None if value is None else int(value)


def _instant(value: datetime | None) -> Instant | None:
    return None if value is None else Instant(value)


async def _subject(services: Services, caller: Caller, subject: SubjectRequest) -> GrantSubject:
    if isinstance(subject, AccountSubjectRequest):
        return AccountGrantSubject(
            await services.accounts.resolve_public_id(subject.account_id, context=caller.context())
        )
    return ExternalRoleGrantSubject(guild_id=int(subject.guild_id), discord_role_id=int(subject.discord_role_id))


async def _accounts(services: Services, caller: Caller, ids: Iterable[int | None]) -> dict[int, UUID]:
    return await services.accounts.public_ids({value for value in ids if value is not None}, context=caller.context())


@get(
    "/nodes",
    responses=responses(401, 403),
    operation_id="permissions_nodes",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.node.view",)),
    guards=[requires(PERM_NODE_VIEW)],
)
async def list_nodes() -> list[PermissionDomainDetail]:
    """List every grantable node, grouped by its first name segment."""
    return PermissionDomainDetail.catalogue(list(CATALOGUE.values()))


@get(
    "/grants",
    responses=responses(400, 401, 403, 404, 422),
    operation_id="permissions_grants_list",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.subject.inspect",)),
    guards=[requires(PERM_SUBJECT_INSPECT)],
)
async def list_grants(
    admin: PermissionAdmin,
    services: Services,
    caller: CurrentCaller,
    account_id: AccountQuery = None,
    discord_role_id: DiscordRoleQuery = None,
    guild_id: GuildQuery = None,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[GrantDetail]:
    """List stored grants, oldest first. Grants are granted and revoked, never edited."""
    query = await SubjectQuery.read(services, caller, account_id, discord_role_id, guild_id)
    grants = await admin.rules_for(
        context=caller.context(),
        account_id=query.account_id,
        discord_role_id=query.discord_role_id,
        guild_id=query.guild_id,
    )
    page = offset_page(grants, offset=offset or 0, page_size=page_size)
    accounts = await _accounts(
        services,
        caller,
        (id_ for grant in page.items for id_ in (grant.subject_account_id, grant.granted_by_account_id)),
    )
    return Page[GrantDetail].from_domain(page, lambda grant: GrantDetail.of(grant, accounts))


@post(
    "/grants",
    status_code=201,
    responses=responses(400, 401, 403, 404, 409, 422),
    operation_id="permissions_grants_create",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("perm.grant.guild",)),
    guards=[requires(PERM_GRANT_GUILD)],
)
async def create_grant(
    data: JSONBody[GrantRequest], admin: PermissionAdmin, services: Services, caller: CurrentCaller
) -> GrantDetail:
    """Grant, deny or forbid a pattern, replacing any rule for the same subject, pattern and scope.

    Actors can grant only what they hold. Forbid is owner-only and needs a reason.
    """
    caller.require_account_id()
    subject = await _subject(services, caller, data.subject)
    scope = _int(data.scope_guild_id)
    await admin.grant(
        context=caller.context(),
        subject=subject,
        pattern=data.pattern,
        effect=_EFFECTS[data.effect],
        scope_guild_id=scope,
        expires_at=_instant(data.expires_at),
        reason=data.reason,
    )
    stored = next(
        grant
        for grant in await admin.rules_for(
            context=caller.context(), account_id=subject.account_id, discord_role_id=subject.discord_role_id
        )
        if grant.pattern == data.pattern and grant.scope_guild_id == scope
    )
    return GrantDetail.of(
        stored, await _accounts(services, caller, (stored.subject_account_id, stored.granted_by_account_id))
    )


@delete(
    "/grants/{grant_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 409, 422),
    operation_id="permissions_grants_revoke",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("perm.grant.guild",)),
    guards=[requires(PERM_GRANT_GUILD)],
)
async def revoke_grant(grant_id: FromPath[int], admin: PermissionAdmin, caller: CurrentCaller) -> None:
    """Revoke a grant. Removing the last direct holder of `perm.grant.global` answers 409."""
    caller.require_account_id()
    await admin.revoke_stored(grant_id, context=caller.context())


@get(
    "/roles",
    responses=responses(400, 401, 403, 422),
    operation_id="permissions_roles_list",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.subject.inspect",)),
    guards=[requires(PERM_SUBJECT_INSPECT)],
)
async def list_roles(
    admin: PermissionAdmin,
    caller: CurrentCaller,
    guild_id: GuildQuery = None,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[RoleDetail]:
    """List global roles, and one guild's own roles when `guild_id` is given, highest rank first."""
    roles = await admin.roles(context=caller.context(), guild_id=_int(guild_id))
    return Page[RoleDetail].from_domain(offset_page(roles, offset=offset or 0, page_size=page_size), RoleDetail.of)


@get(
    "/roles/{role_id:int}",
    responses=responses(401, 403, 404, 422),
    operation_id="permissions_roles_get",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.subject.inspect",)),
    guards=[requires(PERM_SUBJECT_INSPECT)],
)
async def get_role(role_id: FromPath[int], admin: PermissionAdmin, caller: CurrentCaller) -> RoleDetail:
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@post(
    "/roles",
    status_code=201,
    responses=responses(400, 401, 403, 409, 422),
    operation_id="permissions_roles_create",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def create_role(data: JSONBody[RoleCreateRequest], admin: PermissionAdmin, caller: CurrentCaller) -> RoleDetail:
    """Create an empty role below the actor's own rank; add patterns separately."""
    caller.require_account_id()
    role = await admin.create_role(
        context=caller.context(),
        slug=data.slug,
        name=data.name,
        description=data.description,
        guild_id=_int(data.guild_id),
        rank=data.rank,
    )
    return RoleDetail.of(await admin.role_by_id(role.id, context=caller.context()))


@patch(
    "/roles/{role_id:int}",
    responses=responses(400, 401, 403, 404, 422),
    operation_id="permissions_roles_rank",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def set_role_rank(
    role_id: FromPath[int], data: JSONBody[RoleRankRequest], admin: PermissionAdmin, caller: CurrentCaller
) -> RoleDetail:
    """Change who may manage the role. Rank never changes which rule wins."""
    caller.require_account_id()
    await admin.set_rank(await admin.role_by_id(role_id, context=caller.context()), data.rank, context=caller.context())
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@delete(
    "/roles/{role_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 422),
    operation_id="permissions_roles_delete",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def delete_role(role_id: FromPath[int], admin: PermissionAdmin, caller: CurrentCaller) -> None:
    caller.require_account_id()
    await admin.delete_role(await admin.role_by_id(role_id, context=caller.context()), context=caller.context())


@post(
    "/roles/{role_id:int}/patterns",
    status_code=200,
    responses=responses(400, 401, 403, 404, 422),
    operation_id="permissions_roles_add_pattern",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def add_role_pattern(
    role_id: FromPath[int], data: JSONBody[RolePatternRequest], admin: PermissionAdmin, caller: CurrentCaller
) -> RoleDetail:
    """Include or exclude a pattern. Including needs every node it reaches; excluding only narrows the role."""
    caller.require_account_id()
    mode = PatternMode.INCLUDE if data.mode == "include" else PatternMode.EXCLUDE
    role = await admin.role_by_id(role_id, context=caller.context())
    await admin.add_pattern(role, data.pattern, context=caller.context(), mode=mode)
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@delete(
    "/roles/{role_id:int}/patterns",
    status_code=200,
    responses=responses(400, 401, 403, 404, 422),
    operation_id="permissions_roles_remove_pattern",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def remove_role_pattern(
    role_id: FromPath[int],
    pattern: Annotated[str, QueryParameter(min_length=1, max_length=200)],
    admin: PermissionAdmin,
    caller: CurrentCaller,
) -> RoleDetail:
    """Remove an included or excluded pattern."""
    caller.require_account_id()
    await admin.remove_pattern(
        await admin.role_by_id(role_id, context=caller.context()), pattern, context=caller.context()
    )
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@post(
    "/roles/{role_id:int}/includes",
    status_code=200,
    responses=responses(400, 401, 403, 404, 409, 422),
    operation_id="permissions_roles_add_include",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def add_role_include(
    role_id: FromPath[int], data: JSONBody[IncludedRoleRequest], admin: PermissionAdmin, caller: CurrentCaller
) -> RoleDetail:
    """Compose another role into this one. A cycle answers 409."""
    caller.require_account_id()
    role = await admin.role_by_id(role_id, context=caller.context())
    included = await admin.role_by_id(data.role_id, context=caller.context())
    await admin.add_include(role, included, context=caller.context())
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@delete(
    "/roles/{role_id:int}/includes/{included_id:int}",
    status_code=200,
    responses=responses(401, 403, 404, 422),
    operation_id="permissions_roles_remove_include",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def remove_role_include(
    role_id: FromPath[int], included_id: FromPath[int], admin: PermissionAdmin, caller: CurrentCaller
) -> RoleDetail:
    caller.require_account_id()
    role = await admin.role_by_id(role_id, context=caller.context())
    included = await admin.role_by_id(included_id, context=caller.context())
    await admin.remove_include(role, included, context=caller.context())
    return RoleDetail.of(await admin.role_by_id(role_id, context=caller.context()))


@get(
    "/assignments",
    responses=responses(400, 401, 403, 404, 422),
    operation_id="permissions_assignments_list",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.subject.inspect",)),
    guards=[requires(PERM_SUBJECT_INSPECT)],
)
async def list_assignments(
    admin: PermissionAdmin,
    services: Services,
    caller: CurrentCaller,
    account_id: AccountQuery = None,
    discord_role_id: DiscordRoleQuery = None,
    guild_id: GuildQuery = None,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[AssignmentDetail]:
    """List role assignments, oldest first. A `guild_id` filter keeps every account assignment."""
    query = await SubjectQuery.read(services, caller, account_id, discord_role_id, guild_id)
    assignments = await admin.assignments_for(
        context=caller.context(),
        account_id=query.account_id,
        discord_role_id=query.discord_role_id,
        guild_id=query.guild_id,
    )
    page = offset_page(assignments, offset=offset or 0, page_size=page_size)
    accounts = await _accounts(
        services, caller, (id_ for row in page.items for id_ in (row.subject_account_id, row.granted_by_account_id))
    )
    return Page[AssignmentDetail].from_domain(page, lambda row: AssignmentDetail.of(row, accounts))


@post(
    "/assignments",
    status_code=201,
    responses=responses(400, 401, 403, 404, 409, 422),
    operation_id="permissions_assignments_create",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def create_assignment(
    data: JSONBody[AssignmentRequest], admin: PermissionAdmin, services: Services, caller: CurrentCaller
) -> AssignmentDetail:
    """Give a role below the actor's rank, and only if the actor holds everything it confers."""
    caller.require_account_id()
    subject = await _subject(services, caller, data.subject)
    role = await admin.role_by_id(data.role_id, context=caller.context())
    scope = _int(data.scope_guild_id)
    await admin.assign(
        role,
        context=caller.context(),
        subject=subject,
        scope_guild_id=scope,
        expires_at=_instant(data.expires_at),
        reason=data.reason,
    )
    stored = next(
        row
        for row in await admin.assignments_for(
            context=caller.context(), account_id=subject.account_id, discord_role_id=subject.discord_role_id
        )
        if row.role_id == role.id and row.scope_guild_id == scope
    )
    return AssignmentDetail.of(
        stored, await _accounts(services, caller, (stored.subject_account_id, stored.granted_by_account_id))
    )


@delete(
    "/assignments/{assignment_id:int}",
    status_code=204,
    responses=responses(401, 403, 404, 409, 422),
    operation_id="permissions_assignments_delete",
    before_request=reserve_idempotent_before_request,
    security=_WRITE,
    opt=contract(cli=transport_only(), scopes=("role.definition.manage_guild",)),
    guards=[requires(ROLE_DEFINITION_MANAGE_GUILD)],
)
async def delete_assignment(assignment_id: FromPath[int], admin: PermissionAdmin, caller: CurrentCaller) -> None:
    """Take a role back. The owner role cannot be unassigned."""
    caller.require_account_id()
    await admin.unassign_stored(assignment_id, context=caller.context())


@get(
    "/explain",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="permissions_explain",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.subject.inspect",)),
    guards=[requires(PERM_SUBJECT_INSPECT)],
)
async def explain(
    admin: PermissionAdmin,
    services: Services,
    caller: CurrentCaller,
    account_id: AccountQuery = None,
    discord_role_id: DiscordRoleQuery = None,
    guild_id: GuildQuery = None,
    node: Annotated[list[str] | None, QueryParameter(description="Explain only these nodes.")] = None,
) -> list[DecisionDetail]:
    """Explain why a subject is allowed or denied each node.

    Name an account, or a Discord role with its `guild_id`. With `guild_id`, an
    account's member roles in that guild count too, which needs Discord. Callers
    without global authority see only guild-scoped nodes.
    """
    query = await SubjectQuery.read(services, caller, account_id, discord_role_id, guild_id)
    decisions = await admin.explain(
        query.subject(), context=caller.context(), guild_id=query.guild_id, nodes=tuple(node or ())
    )
    return [DecisionDetail.of(decision) for decision in decisions]


@get(
    "/audit",
    responses=responses(400, 401, 403, 422),
    operation_id="permissions_audit",
    security=_READ,
    opt=contract(cli=transport_only(), scopes=("perm.audit.view",)),
    guards=[requires(PERM_AUDIT_VIEW)],
)
async def list_audit(
    admin: PermissionAdmin,
    services: Services,
    caller: CurrentCaller,
    guild_id: GuildQuery = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    cursor: CursorParam = None,
) -> Page[AuditEntryDetail]:
    """List permission changes newest first. Anchors keep a reader's place while changes arrive."""
    page = await admin.audit_page(
        context=caller.context(),
        guild_id=_int(guild_id),
        selector=resolve_selector(offset=offset, cursor=cursor),
        page_size=page_size,
    )
    accounts = await _accounts(
        services,
        caller,
        (
            id_
            for entry in page.items
            for id_ in (entry.actor_account_id, entry.subject_id if entry.subject_kind == "account" else None)
        ),
    )
    return Page[AuditEntryDetail].from_domain(page, lambda entry: AuditEntryDetail.of(entry, accounts))


router = Router(
    dependencies={"admin": Provide(get_permission_admin, sync_to_thread=False)},
    opt={"x-squid-private-response": True},
    path="/permissions",
    tags=["permissions"],
    route_handlers=[
        list_nodes,
        list_grants,
        create_grant,
        revoke_grant,
        list_roles,
        get_role,
        create_role,
        set_role_rank,
        delete_role,
        add_role_pattern,
        remove_role_pattern,
        add_role_include,
        remove_role_include,
        list_assignments,
        create_assignment,
        delete_assignment,
        explain,
        list_audit,
    ],
)
