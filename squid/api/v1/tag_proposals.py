"""Showcase tag proposals and restriction alias edits."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from litestar import get, post
from litestar.di import NamedDependency, Provide
from litestar.handlers import HTTPRouteHandler
from litestar.params import FromPath, FromQuery, JSONBody, SkipValidation
from pydantic import BaseModel, Field, StringConstraints

from squid.api.contract import DEVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import CurrentCaller, Services
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request, reserve_idempotent_request
from squid.api.pagination import CursorParam, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.private_responses import private_response
from squid.api.security import requires
from squid.core.edits.domain import EditCommand, Revision
from squid.core.pagination import ChronologicalSort
from squid.core.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid.core.permissions.domain.models import PermissionNode
from squid.core.tags.application.edits import (
    ALIAS_EDIT,
    MODERATION_EDIT,
    PROPOSAL_EDIT,
    AliasValues,
    ModerationValues,
    ProposalValues,
    tag_target,
)
from squid.core.tags.application.services import normalize_proposal
from squid.core.tags.domain import TagModerationStatus, TagValueType
from squid.core.tags.models import TagDefinition


class TagProposalRequest(BaseModel):
    name: str
    value_type: TagValueType = TagValueType.NONE
    query_name: str | None = None


class TagRevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class TagAliasRequest(TagRevisionRequest):
    alias: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class TagEditResponse(BaseModel):
    id: int
    revision: int


class TagProposalDetail(TagEditResponse):
    name: str
    query_name: str | None
    value_type: TagValueType
    status: TagModerationStatus
    created_at: datetime
    created_by_account_id: int | None

    @classmethod
    def from_definition(cls, tag: TagDefinition) -> TagProposalDetail:
        return cls(
            id=tag.id,
            revision=tag.revision,
            name=tag.display_name,
            query_name=tag.query_name,
            value_type=tag.value_type,
            status=tag.moderation_status,
            created_at=tag.created_at.to_stdlib(),
            created_by_account_id=tag.created_by_account_id,
        )


@get(
    "/proposals",
    responses=responses(400, 401, 403, 422),
    operation_id="tag_proposals_list",
    security=[WEB, DEVICE],
    guards=[requires(TAG_PROPOSAL_LIST)],
    opt=contract(cli=transport_only(), scopes=(TAG_PROPOSAL_LIST.name,)),
)
@private_response
async def list_proposals(
    services: Services,
    caller: CurrentCaller,
    status: FromQuery[TagModerationStatus] = TagModerationStatus.PENDING,
    sort: FromQuery[ChronologicalSort] = ChronologicalSort.OLDEST,
    page_size: PageSizeParam = 20,
    cursor: CursorParam = None,
    offset: OffsetParam = None,
) -> Page[TagProposalDetail]:
    """Page showcase proposals in the selected moderation state."""
    caller.require_account_id()
    rows = await services.tags.list_proposals(status, context=caller.context())
    page = sort.page(
        rows,
        selector=resolve_selector(cursor=cursor, offset=offset),
        page_size=page_size,
        identifier=lambda row: row.id,
        created_at=lambda row: row.created_at,
    )
    return Page[TagProposalDetail].from_domain(page, TagProposalDetail.from_definition)


@post(
    "/proposals",
    operation_id="tag_proposal_create",
    responses=responses(400, 401, 403, 409, 422),
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only()),
    dependencies={"_reservation": Provide(reserve_idempotent_request)},
)
async def create_proposal(
    data: JSONBody[TagProposalRequest],
    services: Services,
    caller: CurrentCaller,
    _reservation: NamedDependency[SkipValidation[None]],
) -> TagEditResponse:
    """Propose a showcase tag for staff review."""
    caller.require_account_id()
    name, query = normalize_proposal(data.name, data.query_name)
    values = ProposalValues(display_name=name, value_type=data.value_type, query_name=query)
    identity = await services.tags.reserve_identity(context=caller.context())
    result = await services.edits.execute(
        EditCommand(PROPOSAL_EDIT, (Revision(tag_target(identity), 0),), values.edit_values()), context=caller.context()
    )
    return TagEditResponse(id=identity, revision=result.outcome.revisions[0].value)


_EDIT_DEPS = {
    "_reservation": Provide(reserve_edit_request),
    "operation_id": Provide(edit_operation_id, sync_to_thread=False),
}


def moderation_route(action: str, status: TagModerationStatus, permission: PermissionNode) -> HTTPRouteHandler:
    @post(
        f"/proposals/{{tag_id:int}}/{action}",
        operation_id=f"tag_proposal_{action}",
        status_code=200,
        responses=responses(400, 401, 403, 404, 409, 412, 422),
        security=[WEB_WRITE, DEVICE],
        opt=contract(cli=transport_only(), scopes=(permission.name,)),
        guards=[requires(permission)],
        dependencies=_EDIT_DEPS,
    )
    async def moderate(
        tag_id: FromPath[int],
        data: JSONBody[TagRevisionRequest],
        services: Services,
        caller: CurrentCaller,
        operation_id: NamedDependency[SkipValidation[UUID]],
    ) -> TagEditResponse:
        caller.require_account_id()
        result = await services.edits.execute(
            EditCommand(
                MODERATION_EDIT,
                (Revision(tag_target(tag_id), data.revision),),
                ModerationValues(status=status).edit_values(),
                operation_id,
            ),
            context=caller.context(),
        )
        return TagEditResponse(id=tag_id, revision=result.outcome.revisions[0].value)

    return moderate


approve = moderation_route("approve", TagModerationStatus.APPROVED, TAG_PROPOSAL_APPROVE)
reject = moderation_route("reject", TagModerationStatus.REJECTED, TAG_PROPOSAL_REJECT)
archive = moderation_route("archive", TagModerationStatus.ARCHIVED, TAG_PROPOSAL_ARCHIVE)


@post(
    "/{tag_id:int}/aliases",
    operation_id="tag_alias_create",
    responses=responses(400, 401, 403, 404, 409, 412, 422),
    security=[WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=(RESTRICTION_ALIAS_CREATE.name,)),
    guards=[requires(RESTRICTION_ALIAS_CREATE)],
    dependencies=_EDIT_DEPS,
)
async def add_alias(
    tag_id: FromPath[int],
    data: JSONBody[TagAliasRequest],
    services: Services,
    caller: CurrentCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
) -> TagEditResponse:
    """Add an alternate name to an official restriction."""
    caller.require_account_id()
    result = await services.edits.execute(
        EditCommand(
            ALIAS_EDIT,
            (Revision(tag_target(tag_id), data.revision),),
            AliasValues(alias=data.alias).edit_values(),
            operation_id,
        ),
        context=caller.context(),
    )
    return TagEditResponse(id=tag_id, revision=result.outcome.revisions[0].value)
