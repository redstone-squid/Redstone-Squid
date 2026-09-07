"""Explicit review and approval of persisted recalculation candidates."""

from copy import deepcopy
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from whenever import Instant

from squid.builds.application import BuildEditor, BuildService
from squid.builds.domain import Build, BuildDraft
from squid.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.permissions.application import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECALC
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal, revision_values


class RevisionProposalRepository(Protocol):
    """Retain candidates and atomically commit their approved build revision."""

    async def create(self, proposal: RevisionProposal) -> RevisionProposal: ...
    async def get(self, proposal_id: UUID) -> RevisionProposal | None: ...
    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RevisionProposal, ...]: ...
    async def match(self, proposal: RevisionProposal) -> RevisionProposal: ...
    async def approve(self, proposal: RevisionProposal, build: Build, actor: BuildEditor) -> RevisionProposal: ...


class RevisionProposalService:
    """Keep inference separate from target matching and explicit authorized approval."""

    def __init__(
        self, repository: RevisionProposalRepository, builds: BuildService, permissions: PermissionService
    ) -> None:
        self._repository = repository
        self._builds = builds
        self._permissions = permissions

    async def create(
        self,
        *,
        run_id: UUID,
        index: int,
        owner_account_id: int,
        actor: Subject,
        source_message_id: int,
        candidate: BuildDraft,
    ) -> RevisionProposal:
        """Retain one stable candidate; inference alone never writes the target build."""
        if actor.account_id is None or not await self._permissions.allows(actor, BUILD_SUBMISSION_RECALC):
            raise AuthorizationError
        now = Instant.now()
        return await self._repository.create(
            RevisionProposal(
                uuid5(run_id, str(index)),
                run_id,
                owner_account_id,
                actor.account_id,
                source_message_id,
                RevisionFacts.from_draft(candidate),
                None,
                None,
                {},
                {},
                now,
                now.add(days=7, days_assumed_24h_ok=True),
            )
        )

    async def get(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        """Read a retained proposal under current owner or staff authority."""
        proposal = await self._repository.get(proposal_id)
        if proposal is None:
            raise NotFoundError
        if actor.account_id not in {
            proposal.owner_account_id,
            proposal.requested_by_account_id,
        } and not await self._permissions.allows(actor, BUILD_SUBMISSION_EDIT):
            raise AuthorizationError
        return proposal

    async def list_for_source(self, source_message_id: int, actor: Subject) -> tuple[RevisionProposal, ...]:
        """Return a bounded history with the same visibility policy as individual reads."""
        rows = await self._repository.list_for_source(source_message_id, limit=20)
        return tuple([await self.get(row.id, actor) for row in rows])

    async def match(self, proposal_id: UUID, build_id: int, actor: Subject, *, renew: bool = False) -> RevisionProposal:
        """Capture a fresh diff; renewed review creates a new immutable proposal identity."""
        from dataclasses import replace

        proposal = await self.get(proposal_id, actor)
        self._require_pending(proposal)
        build = await self._builds.get(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        await self._builds.authorize_edit(BuildEditor(actor), build)
        if build.submitter_account_id != proposal.owner_account_id:
            raise AuthorizationError
        if build_id not in await self._builds.list_ids_for_source_message(proposal.source_message_id):
            message = "Choose a build linked to this source message."
            raise ValidationError(message)
        before = revision_values(build)
        await self._builds.prepare_edit(BuildEditor(actor), build, proposal.facts.patch(build))
        matched = replace(
            proposal, build_id=build_id, expected_revision=build.revision, before=before, after=revision_values(build)
        )
        if renew:
            matched = replace(matched, id=uuid4(), approved_by_account_id=None, applied_revision=None)
            return await self._repository.create(matched)
        return await self._repository.match(matched)

    async def approve(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        """Apply exactly the reviewed revision under the existing owner-pending/staff policy."""
        if actor.account_id is None:
            raise AuthorizationError
        proposal = await self.get(proposal_id, actor)
        if proposal.applied_revision is not None:
            return proposal
        self._require_pending(proposal)
        if proposal.build_id is None or proposal.expected_revision is None:
            message = "Match the candidate to a build and review its diff first."
            raise ValidationError(message)
        build = await self._builds.get(proposal.build_id)
        if build is None:
            raise BuildNotFoundError(proposal.build_id)
        if build.revision != proposal.expected_revision:
            raise BuildRevisionMismatchError(
                build.id or proposal.build_id,
                expected_revision=proposal.expected_revision,
                current_revision=build.revision,
            )
        prepared = await self._builds.prepare_edit(BuildEditor(actor), deepcopy(build), proposal.facts.patch(build))
        if revision_values(prepared) != proposal.after:
            message = "Resolved build facts changed since review; renew the proposal before approving. "
            raise ConflictError(message)
        return await self._repository.approve(proposal, prepared, BuildEditor(actor))

    @staticmethod
    def _require_pending(proposal: RevisionProposal) -> None:
        if proposal.expires_at <= Instant.now() or proposal.applied_revision is not None:
            message = "This proposal is no longer pending review."
            raise ConflictError(message)
