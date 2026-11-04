"""Explicit review and approval of persisted recalculation candidates."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from whenever import Instant

from squid.builds.application import BuildEditor, BuildService
from squid.builds.application.ports import BuildUnitOfWork
from squid.builds.domain import BuildDraft
from squid.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.builds.models import Build
from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.permissions.application import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECALC
from squid.submissions.application.revision_values import RevisionFacts, RevisionProposal, revision_values
from squid.unit_of_work import UnitOfWorkFactory


class RevisionProposalRepository(Protocol):
    """Retain candidates and atomically commit their approved build revision."""

    async def create(self, proposal: RevisionProposal) -> RevisionProposal: ...
    async def get(self, proposal_id: UUID) -> RevisionProposal | None: ...
    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RevisionProposal, ...]: ...
    async def match(self, proposal: RevisionProposal) -> RevisionProposal: ...
    async def get_for_update(self, proposal_id: UUID) -> RevisionProposal | None: ...
    async def mark_approved(self, proposal_id: UUID, *, account_id: int, revision: int) -> RevisionProposal: ...


class RevisionProposalService:
    """Review new inference results as proposed edits to an existing build.

    First retain the inferred candidate, then match it to a build from the same
    source message to capture a before/after diff. Approval applies that reviewed
    diff only while the build revision and resolved values still match. The saved
    proposal records who approved it and the resulting build revision, allowing
    repeated approval requests to return the same result.
    """

    def __init__(
        self,
        builds: BuildService,
        permissions: PermissionService,
        *,
        uow_factory: UnitOfWorkFactory[RevisionUnitOfWork],
    ) -> None:
        self._uow_factory = uow_factory
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
        async with self._uow_factory() as uow:
            if actor.account_id is None or not await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_RECALC):
                raise AuthorizationError
            now = Instant.now()
            result = await uow.revision_proposals.create(
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
            await uow.commit()
            return result

    async def get(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        """Read a retained proposal under current owner or staff authority."""
        async with self._uow_factory() as uow:
            proposal = await uow.revision_proposals.get(proposal_id)
            if proposal is None:
                raise NotFoundError
            await self._require_read_access(uow, (proposal,), actor)
            return proposal

    async def list_for_source(self, source_message_id: int, actor: Subject) -> tuple[RevisionProposal, ...]:
        """Return a bounded history with the same visibility policy as individual reads."""
        async with self._uow_factory() as uow:
            rows = await uow.revision_proposals.list_for_source(source_message_id, limit=20)
            await self._require_read_access(uow, rows, actor)
            return rows

    async def _require_read_access(
        self, uow: RevisionUnitOfWork, proposals: Sequence[RevisionProposal], actor: Subject
    ) -> None:
        if any(
            actor.account_id not in {proposal.owner_account_id, proposal.requested_by_account_id}
            for proposal in proposals
        ) and not await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_EDIT):
            raise AuthorizationError

    async def match(self, proposal_id: UUID, build_id: int, actor: Subject, *, renew: bool = False) -> RevisionProposal:
        """Capture a fresh diff; renewed review creates a new immutable proposal identity."""
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
        async with self._uow_factory() as uow:
            await self._require_read_access(uow, (proposal,), actor)
            await self._builds.authorize_edit_in(uow, BuildEditor(actor), build)
            if renew:
                matched = replace(matched, id=uuid4(), approved_by_account_id=None, applied_revision=None)
                result = await uow.revision_proposals.create(matched)
            else:
                result = await uow.revision_proposals.match(matched)
            await uow.commit()
            return result

    async def approve(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        """Apply the reviewed changes after rechecking access and the build revision.

        An already applied proposal returns its receipt. A changed build or newly
        resolved facts require another review; approval never silently updates
        the retained diff.
        """
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
        prepared = await self._builds.prepare_edit(
            BuildEditor(actor), build.copy_for_edit(), proposal.facts.patch(build)
        )
        if revision_values(prepared) != proposal.after:
            message = "Resolved build facts changed since review; renew the proposal before approving. "
            raise ConflictError(message)
        return await self._approve_prepared(proposal, prepared, BuildEditor(actor))

    async def _approve_prepared(self, proposal: RevisionProposal, build: Build, actor: BuildEditor) -> RevisionProposal:
        async with self._uow_factory() as uow:
            # Use account merging's lock order: build first, then proposal, to avoid deadlocks.
            current = await uow.builds.get_for_update(proposal.build_id or 0)
            if current is None:
                raise BuildNotFoundError(proposal.build_id or 0)
            retained = await uow.revision_proposals.get_for_update(proposal.id)
            if retained is None:
                raise NotFoundError
            await self._require_read_access(uow, (retained,), actor.subject)
            if retained.applied_revision is not None:
                return retained
            if retained.expires_at <= Instant.now() or (retained.build_id, retained.expected_revision) != (
                proposal.build_id,
                proposal.expected_revision,
            ):
                message = "The proposal is no longer the reviewed revision."
                raise ConflictError(message)
            if current.revision != proposal.expected_revision:
                raise BuildRevisionMismatchError(
                    current.id or 0, expected_revision=proposal.expected_revision, current_revision=current.revision
                )
            if (current.submitter_account_id, current.category, current.submission_status) != (
                build.submitter_account_id,
                build.category,
                build.submission_status,
            ) or retained.owner_account_id != build.submitter_account_id:
                raise AuthorizationError
            await self._builds.authorize_edit_in(uow, actor, current)
            if actor.subject.account_id is None:
                raise AuthorizationError
            build.edited_time = Instant.now()
            await uow.builds.save(build)
            result = await uow.revision_proposals.mark_approved(
                proposal.id, account_id=actor.subject.account_id, revision=build.revision
            )
            await uow.commit()
            return result

    @staticmethod
    def _require_pending(proposal: RevisionProposal) -> None:
        if proposal.expires_at <= Instant.now() or proposal.applied_revision is not None:
            message = "This proposal is no longer pending review."
            raise ConflictError(message)


class RevisionUnitOfWork(BuildUnitOfWork, Protocol):
    @property
    def revision_proposals(self) -> RevisionProposalRepository: ...
