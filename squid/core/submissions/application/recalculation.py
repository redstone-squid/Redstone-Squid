"""Explicit review and approval of persisted recalculation candidates."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from whenever import Instant

from squid.core.actors import ActingContext, Actor
from squid.core.builds.application import BuildService
from squid.core.builds.application.ports import BuildUnitOfWork
from squid.core.builds.domain import BuildInput
from squid.core.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.core.builds.models import Build
from squid.core.errors import (
    AccountRequiredError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    OwnershipRequiredError,
    ValidationError,
)
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECALC
from squid.core.submissions.application.recalculation_values import (
    RecalculationFacts,
    RecalculationProposal,
    RecalculationProposalFilters,
    RecalculationProposalSort,
    recalculation_values,
)
from squid.core.unit_of_work import UnitOfWorkFactory


class RecalculationProposalRepository(Protocol):
    """Retain candidates and atomically commit their approved build revision."""

    async def create(self, proposal: RecalculationProposal) -> RecalculationProposal: ...
    async def get(self, proposal_id: UUID) -> RecalculationProposal | None: ...
    async def list_for_source(self, source_message_id: int, *, limit: int) -> tuple[RecalculationProposal, ...]: ...
    async def list_page(
        self,
        filters: RecalculationProposalFilters,
        *,
        sort: RecalculationProposalSort,
        selector: PageSelector[UUID],
        limit: int,
        now: Instant,
    ) -> tuple[RecalculationProposal, ...]: ...
    async def count(self, filters: RecalculationProposalFilters, *, now: Instant) -> int: ...
    async def match(self, proposal: RecalculationProposal) -> RecalculationProposal: ...
    async def get_for_update(self, proposal_id: UUID) -> RecalculationProposal | None: ...
    async def mark_approved(self, proposal_id: UUID, *, account_id: int, revision: int) -> RecalculationProposal: ...


class RecalculationProposalService:
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
        context: ActingContext,
        source_message_id: int,
        candidate: BuildInput,
    ) -> RecalculationProposal:
        """Retain one stable candidate; inference alone never writes the target build."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                if actor.linked_account_id is None:
                    raise AccountRequiredError
                await self._permissions.require(actor, BUILD_SUBMISSION_RECALC, uow=uow)
                now = Instant.now()
                result = await uow.recalculation_proposals.create(
                    RecalculationProposal(
                        uuid5(run_id, str(index)),
                        run_id,
                        owner_account_id,
                        actor.linked_account_id,
                        source_message_id,
                        RecalculationFacts.from_draft(candidate),
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

    async def get(self, proposal_id: UUID, *, context: ActingContext) -> RecalculationProposal:
        """Read a retained proposal under current owner or staff authority."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                proposal = await uow.recalculation_proposals.get(proposal_id)
                if proposal is None:
                    raise NotFoundError
                await self._require_read_access(uow, (proposal,), actor)
                return proposal

    async def list_for_source(
        self, source_message_id: int, *, context: ActingContext
    ) -> tuple[RecalculationProposal, ...]:
        """Return a bounded history with the same visibility policy as individual reads."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                rows = await uow.recalculation_proposals.list_for_source(source_message_id, limit=20)
                await self._require_read_access(uow, rows, actor)
                return rows

    async def list_page(
        self,
        filters: RecalculationProposalFilters,
        *,
        sort: RecalculationProposalSort = RecalculationProposalSort.ID,
        selector: PageSelector[UUID] = FIRST_PAGE,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[RecalculationProposal, UUID]:
        """Page every proposal for staff review; an anchor outside the listing raises PageAnchorNotFoundError."""
        actor = context.effective
        with context.scope():
            async with self._uow_factory() as uow:
                await self._permissions.require(actor, BUILD_SUBMISSION_EDIT, uow=uow)
                now = Instant.now()
                rows = await uow.recalculation_proposals.list_page(
                    filters, sort=sort, selector=selector, limit=page_size + 1, now=now
                )
                return keyset_page(
                    rows,
                    selector=selector,
                    page_size=page_size,
                    total=await uow.recalculation_proposals.count(filters, now=now),
                    id_of=lambda proposal: proposal.id,
                )

    async def _require_read_access(
        self, uow: RevisionUnitOfWork, proposals: Sequence[RecalculationProposal], actor: Actor
    ) -> None:
        if any(
            actor.linked_account_id not in {proposal.owner_account_id, proposal.requested_by_account_id}
            for proposal in proposals
        ):
            await self._permissions.require(actor, BUILD_SUBMISSION_EDIT, uow=uow)

    async def match(
        self, proposal_id: UUID, build_id: int, *, context: ActingContext, renew: bool = False
    ) -> RecalculationProposal:
        """Capture a fresh diff; renewed review creates a new immutable proposal identity."""
        actor = context.effective
        with context.scope():
            proposal = await self.get(proposal_id, context=context)
            self._require_pending(proposal)
            build = await self._builds.get(build_id, context=context)
            if build is None:
                raise BuildNotFoundError(build_id)
            await self._builds.authorize_edit(build, context=ActingContext.for_actor(actor))
            if build.submitter_account_id != proposal.owner_account_id:
                raise OwnershipRequiredError
            if build_id not in await self._builds.list_ids_for_source_message(
                proposal.source_message_id, context=context
            ):
                message = "Choose a build linked to this source message."
                raise ValidationError(message)
            before = recalculation_values(build)
            await self._builds.prepare_edit(build, proposal.facts.patch(build), context=ActingContext.for_actor(actor))
            matched = replace(
                proposal,
                build_id=build_id,
                expected_revision=build.revision,
                before=before,
                after=recalculation_values(build),
            )
            async with self._uow_factory() as uow:
                await self._require_read_access(uow, (proposal,), actor)
                await self._builds.authorize_edit(build, uow=uow, context=ActingContext.for_actor(actor))
                if renew:
                    matched = replace(matched, id=uuid4(), approved_by_account_id=None, applied_revision=None)
                    result = await uow.recalculation_proposals.create(matched)
                else:
                    result = await uow.recalculation_proposals.match(matched)
                await uow.commit()
                return result

    async def approve(self, proposal_id: UUID, *, context: ActingContext) -> RecalculationProposal:
        """Apply the reviewed changes after rechecking access and the build revision.

        An already applied proposal returns its receipt. A changed build or newly
        resolved facts require another review; approval never silently updates
        the retained diff.
        """
        actor = context.effective
        with context.scope():
            if actor.linked_account_id is None:
                raise AccountRequiredError
            proposal = await self.get(proposal_id, context=context)
            if proposal.applied_revision is not None:
                return proposal
            self._require_pending(proposal)
            if proposal.build_id is None or proposal.expected_revision is None:
                message = "Match the candidate to a build and review its diff first."
                raise ValidationError(message)
            build = await self._builds.get(proposal.build_id, context=context)
            if build is None:
                raise BuildNotFoundError(proposal.build_id)
            if build.revision != proposal.expected_revision:
                raise BuildRevisionMismatchError(
                    build.id or proposal.build_id,
                    expected=proposal.expected_revision,
                    current=build.revision,
                )
            prepared = await self._builds.prepare_edit(
                build.copy_for_edit(), proposal.facts.patch(build), context=ActingContext.for_actor(actor)
            )
            if recalculation_values(prepared) != proposal.after:
                message = "Resolved build facts changed since review; renew the proposal before approving. "
                raise ConflictError(message)
            return await self._approve_prepared(proposal, prepared, actor)

    async def _approve_prepared(
        self, proposal: RecalculationProposal, build: Build, actor: Actor
    ) -> RecalculationProposal:
        if actor.linked_account_id is None:
            raise AccountRequiredError
        async with (
            self._uow_factory() as uow,
            uow.builds.editing(
                actor=actor.linked_account_id,
                operation="recalculation",
                # Build history stores these values, so they keep the proposal's original name.
                source="revision_proposal",
                correlation_kind="revision_proposal",
                correlation_id=str(proposal.id),
            ),
        ):
            # Use account merging's lock order: build first, then proposal, to avoid deadlocks.
            current = await uow.builds.get_for_update(proposal.build_id or 0)
            if current is None:
                raise BuildNotFoundError(proposal.build_id or 0)
            retained = await uow.recalculation_proposals.get_for_update(proposal.id)
            if retained is None:
                raise NotFoundError
            await self._require_read_access(uow, (retained,), actor)
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
                    current.id or 0, expected=proposal.expected_revision, current=current.revision
                )
            if (current.submitter_account_id, current.category, current.submission_status) != (
                build.submitter_account_id,
                build.category,
                build.submission_status,
            ) or retained.owner_account_id != build.submitter_account_id:
                raise AuthorizationError
            await self._builds.authorize_edit(current, uow=uow, context=ActingContext.for_actor(actor))
            build.edited_time = Instant.now()
            await uow.builds.save(
                build,
            )
            result = await uow.recalculation_proposals.mark_approved(
                proposal.id, account_id=actor.linked_account_id, revision=build.revision
            )
            await uow.commit()
            return result

    @staticmethod
    def _require_pending(proposal: RecalculationProposal) -> None:
        if proposal.expires_at <= Instant.now() or proposal.applied_revision is not None:
            message = "This proposal is no longer pending review."
            raise ConflictError(message)


class RevisionUnitOfWork(BuildUnitOfWork, Protocol):
    @property
    def recalculation_proposals(self) -> RecalculationProposalRepository: ...
