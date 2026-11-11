"""Retained recalculation proposals and explicit build revision approval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from squid.api.contract import DEVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import CurrentCaller, Services
from squid.api.idempotency import reserve_idempotent_request
from squid.core.errors import JSONValue
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal
from squid.submissions.application.revisions import RevisionProposalService


class RevisionProposalResponse(BaseModel):
    """A private candidate, its retained diff, and any committed approval receipt."""

    id: UUID
    run_id: UUID
    source_message_id: int
    facts: RevisionFacts
    build_id: int | None
    expected_revision: int | None
    before: dict[str, JSONValue]
    after: dict[str, JSONValue]
    expires_at: str
    applied_revision: int | None

    @classmethod
    def from_domain(cls, proposal: RevisionProposal) -> RevisionProposalResponse:
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


class RevisionMatchRequest(BaseModel):
    """Choose a source-linked build; renewing review retains the old proposal."""

    build_id: int = Field(gt=0)
    renew: bool = False


def revisions(services: Services) -> RevisionProposalService:
    return services.submission_revisions


Proposals = Annotated[RevisionProposalService, Depends(revisions)]
router = APIRouter(
    prefix="/submissions/revisions", tags=["submissions"], dependencies=[Depends(reserve_idempotent_request)]
)
_READ = contract(security=[WEB, DEVICE], cli=transport_only())
_WRITE = contract(security=[WEB_WRITE, DEVICE], cli=transport_only())


@router.get("/source/{message_id}", operation_id="submission_revision_history", openapi_extra=_READ)
async def history(message_id: int, caller: CurrentCaller, proposals: Proposals) -> list[RevisionProposalResponse]:
    """List revision proposals for a source message after checking the caller's access."""
    caller.require_consented_account()
    return [
        RevisionProposalResponse.from_domain(item)
        for item in await proposals.list_for_source(message_id, caller.to_actor())
    ]


@router.get("/{proposal_id}", operation_id="submission_revision_get", openapi_extra=_READ)
async def get_proposal(proposal_id: UUID, caller: CurrentCaller, proposals: Proposals) -> RevisionProposalResponse:
    """Return a revision proposal after checking the caller's access."""
    caller.require_consented_account()
    return RevisionProposalResponse.from_domain(await proposals.get(proposal_id, caller.to_actor()))


@router.post("/{proposal_id}/match", operation_id="submission_revision_match", openapi_extra=_WRITE)
async def match_proposal(
    proposal_id: UUID, payload: RevisionMatchRequest, caller: CurrentCaller, proposals: Proposals
) -> RevisionProposalResponse:
    """Match a proposal to a source-linked build and capture the diff for review.

    With renew set, create a new proposal and retain the previous review.
    """
    caller.require_consented_account()
    return RevisionProposalResponse.from_domain(
        await proposals.match(proposal_id, payload.build_id, caller.to_actor(), renew=payload.renew)
    )


@router.post("/{proposal_id}/approve", operation_id="submission_revision_approve", openapi_extra=_WRITE)
async def approve_proposal(proposal_id: UUID, caller: CurrentCaller, proposals: Proposals) -> RevisionProposalResponse:
    """Apply the reviewed diff if the build still matches its expected revision.

    An already applied proposal returns its existing approval result.
    """
    caller.require_consented_account()
    return RevisionProposalResponse.from_domain(await proposals.approve(proposal_id, caller.to_actor()))
