"""HTTP access to recalculation proposals: inferred changes to an existing build, held for review.

The routes live under `/submissions/revisions` and their operation IDs say `submission_revision_*`
because clients already call them by those names. They serve the same recalculation proposals.
"""

from uuid import UUID

from litestar import Router, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, JSONBody, SkipValidation
from pydantic import BaseModel, Field

from squid.api.contract import DEVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import CurrentCaller, Services
from squid.api.idempotency import reserve_idempotent_request
from squid.core.errors import JSONValue
from squid.submissions.application.recalculation import RecalculationProposalService
from squid.submissions.application.recalculation_values import RecalculationFacts, RecalculationProposal


class RecalculationProposalResponse(BaseModel):
    """A private candidate, its retained diff, and any committed approval receipt."""

    id: UUID
    run_id: UUID
    source_message_id: int
    facts: RecalculationFacts
    build_id: int | None
    expected_revision: int | None
    before: dict[str, JSONValue]
    after: dict[str, JSONValue]
    expires_at: str
    applied_revision: int | None

    @classmethod
    def from_domain(cls, proposal: RecalculationProposal) -> RecalculationProposalResponse:
        return cls(
            id=proposal.id,
            run_id=proposal.run_id,
            source_message_id=proposal.source_message_id,
            facts=proposal.facts,
            build_id=proposal.build_id,
            expected_revision=proposal.expected_revision,
            before=proposal.before,
            after=proposal.after,
            expires_at=proposal.expires_at.format_iso(),
            applied_revision=proposal.applied_revision,
        )


class RecalculationMatchRequest(BaseModel):
    """Choose a source-linked build; renewing review retains the old proposal."""

    build_id: int = Field(gt=0)
    renew: bool = False


def recalculation_proposals(services: Services) -> RecalculationProposalService:
    return services.submission_recalculations


Proposals = NamedDependency[SkipValidation[RecalculationProposalService]]
_READ = contract(cli=transport_only())
_WRITE = contract(cli=transport_only())


@get(
    "/source/{message_id:int}",
    operation_id="submission_revision_history",
    security=[WEB, DEVICE],
    opt=_READ,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "proposals": Provide(recalculation_proposals, sync_to_thread=False),
    },
)
async def history(
    message_id: FromPath[int],
    caller: CurrentCaller,
    proposals: Proposals,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> list[RecalculationProposalResponse]:
    """List recalculation proposals for a source message after checking the caller's access."""
    caller.require_consented_account()
    return [
        RecalculationProposalResponse.from_domain(item)
        for item in await proposals.list_for_source(message_id, caller.to_actor())
    ]


@get(
    "/{proposal_id:uuid}",
    operation_id="submission_revision_get",
    security=[WEB, DEVICE],
    opt=_READ,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "proposals": Provide(recalculation_proposals, sync_to_thread=False),
    },
)
async def get_proposal(
    proposal_id: FromPath[UUID],
    caller: CurrentCaller,
    proposals: Proposals,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> RecalculationProposalResponse:
    """Return a recalculation proposal after checking the caller's access."""
    caller.require_consented_account()
    return RecalculationProposalResponse.from_domain(await proposals.get(proposal_id, caller.to_actor()))


@post(
    "/{proposal_id:uuid}/match",
    operation_id="submission_revision_match",
    security=[WEB_WRITE, DEVICE],
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "proposals": Provide(recalculation_proposals, sync_to_thread=False),
    },
)
async def match_proposal(
    proposal_id: FromPath[UUID],
    data: JSONBody[RecalculationMatchRequest],
    caller: CurrentCaller,
    proposals: Proposals,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> RecalculationProposalResponse:
    """Match a proposal to a source-linked build and capture the diff for review.

    With renew set, create a new proposal and retain the previous review.
    """
    caller.require_consented_account()
    return RecalculationProposalResponse.from_domain(
        await proposals.match(proposal_id, data.build_id, caller.to_actor(), renew=data.renew)
    )


@post(
    "/{proposal_id:uuid}/approve",
    operation_id="submission_revision_approve",
    security=[WEB_WRITE, DEVICE],
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "proposals": Provide(recalculation_proposals, sync_to_thread=False),
    },
)
async def approve_proposal(
    proposal_id: FromPath[UUID],
    caller: CurrentCaller,
    proposals: Proposals,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> RecalculationProposalResponse:
    """Apply the reviewed diff if the build still matches its expected revision.

    An already applied proposal returns its existing approval result.
    """
    caller.require_consented_account()
    return RecalculationProposalResponse.from_domain(await proposals.approve(proposal_id, caller.to_actor()))


router = Router(
    path="/submissions/revisions",
    tags=["submissions"],
    route_handlers=[history, get_proposal, match_proposal, approve_proposal],
)
