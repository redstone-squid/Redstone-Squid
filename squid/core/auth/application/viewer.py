"""Account-scoped reads for browser sessions and detail pages."""

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from squid.core.accounts.domain.profiles import CreatorViewer
from squid.core.actors import ActingContext, application_operation
from squid.core.builds.models import Build
from squid.core.errors import NotFoundError
from squid.core.notifications.domain import RecordSubscriptionFilter, SubscriptionKind
from squid.core.pagination import Page, PageSelector
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import (
    ACCOUNT_SELF_READ,
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.core.permissions.domain.models import NodeScope
from squid.core.submissions.application.reviews import (
    CreditedBuild,
    SubmissionDetail,
    SubmissionStatus,
    SubmissionSummary,
)
from squid.core.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True)
class SessionUser:
    id: UUID
    """The account's public ID."""

    display_name: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class SessionCounts:
    unread_notifications: int
    drafts: int
    drafts_needing_attention: int
    review_queue: int | None


@dataclass(frozen=True)
class ViewerBallot:
    vote_session_id: int
    option_id: str


class ViewerRepository(Protocol):
    async def portfolio(self, account_id: int, selector: PageSelector, page_size: int) -> Page[CreditedBuild]: ...
    async def submissions(
        self, account_id: int | None, selector: PageSelector, page_size: int, *, status: str | None = None
    ) -> Page[SubmissionSummary]: ...
    async def submission(
        self, identifier: UUID, owner: int | None, review_session_id: int | None = None
    ) -> SubmissionDetail | None: ...
    async def session(self, account_id: int, *, staff: bool) -> tuple[SessionUser, SessionCounts]: ...
    async def build_state(self, account_id: int, build: Build) -> tuple[dict[UUID, int], ViewerBallot | None]: ...
    async def subscription(
        self, account_id: int, kind: SubscriptionKind, target: UUID | RecordSubscriptionFilter
    ) -> int | None: ...
    async def creator(self, account_id: int, public_id: UUID) -> CreatorViewer: ...


class ViewerUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def viewer(self) -> ViewerRepository: ...


class ViewerQueries:
    def __init__(self, uow_factory: UnitOfWorkFactory[ViewerUnitOfWork], permissions: PermissionService):
        self._uow_factory = uow_factory
        self._permissions = permissions

    async def _reader(self, context: ActingContext, uow: ViewerUnitOfWork) -> int:
        account_id = context.effective.require_account_id()
        await self._permissions.require(context.effective, ACCOUNT_SELF_READ, uow=uow)
        return account_id

    @application_operation
    async def portfolio(self, selector: PageSelector, page_size: int, *, context: ActingContext) -> Page[CreditedBuild]:
        async with self._uow_factory() as uow:
            return await uow.viewer.portfolio(await self._reader(context, uow), selector, page_size)

    @application_operation
    async def submissions(
        self, selector: PageSelector, page_size: int, *, status: SubmissionStatus | None = None, context: ActingContext
    ) -> Page[SubmissionSummary]:
        async with self._uow_factory() as uow:
            return await uow.viewer.submissions(await self._reader(context, uow), selector, page_size, status=status)

    @application_operation
    async def review_queue(
        self, selector: PageSelector, page_size: int, *, context: ActingContext
    ) -> Page[SubmissionSummary]:
        async with self._uow_factory() as uow:
            await self._reader(context, uow)
            await self._permissions.require(context.effective, BUILD_SUBMISSION_VIEW_PENDING, uow=uow)
            return await uow.viewer.submissions(None, selector, page_size, status="pending")

    @application_operation
    async def submission(
        self, identifier: UUID, *, context: ActingContext, review_session_id: int | None = None
    ) -> SubmissionDetail:
        async with self._uow_factory() as uow:
            owner = await self._reader(context, uow)
            allowed = await self._permissions.check(
                context.effective,
                BUILD_SUBMISSION_VIEW_PENDING,
                BUILD_SUBMISSION_APPROVE,
                BUILD_SUBMISSION_REJECT,
                uow=uow,
            )
            detail = await uow.viewer.submission(
                identifier,
                None if BUILD_SUBMISSION_VIEW_PENDING.name in allowed.allowed_nodes else owner,
                review_session_id,
            )
            if detail is None:
                raise NotFoundError
            can_decide = (
                detail.submission.kind == "new_build"
                and detail.submission.status == "pending"
                and review_session_id is None
                and bool(detail.reviewed_facts)
            )
            return replace(
                detail,
                can_approve=can_decide and BUILD_SUBMISSION_APPROVE.name in allowed.allowed_nodes,
                can_reject=can_decide and BUILD_SUBMISSION_REJECT.name in allowed.allowed_nodes,
            )

    @application_operation
    async def session(self, *, context: ActingContext) -> tuple[SessionUser, SessionCounts, tuple[str, ...]]:
        async with self._uow_factory() as uow:
            account_id = await self._reader(context, uow)
            nodes = (node for node in self._permissions.catalogue.values() if node.scope == NodeScope.GLOBAL)
            allowed = (await self._permissions.check(context.effective, *nodes, uow=uow)).allowed_nodes
            user, counts = await uow.viewer.session(account_id, staff=BUILD_SUBMISSION_VIEW_PENDING.name in allowed)
            return user, counts, tuple(sorted(allowed))

    @application_operation
    async def build_state(self, build: Build, *, context: ActingContext) -> tuple[dict[UUID, int], ViewerBallot | None]:
        async with self._uow_factory() as uow:
            return await uow.viewer.build_state(await self._reader(context, uow), build)

    @application_operation
    async def subscription(
        self, kind: SubscriptionKind, target: UUID | RecordSubscriptionFilter, *, context: ActingContext
    ) -> int | None:
        async with self._uow_factory() as uow:
            return await uow.viewer.subscription(await self._reader(context, uow), kind, target)

    @application_operation
    async def creator(self, public_id: UUID, *, context: ActingContext) -> CreatorViewer:
        async with self._uow_factory() as uow:
            return await uow.viewer.creator(await self._reader(context, uow), public_id)
