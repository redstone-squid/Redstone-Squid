"""Account-scoped read queries for the browser session and detail pages."""

from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.accounts.domain import avatar_url_for
from squid.core.accounts.domain.profiles import CreatorViewer
from squid.core.accounts.errors import AccountNotFoundError
from squid.core.accounts.models import Account, AccountIdentity, AccountProfile
from squid.core.auth.application.viewer import SessionCounts, SessionUser, ViewerBallot
from squid.core.builds.domain import BuildStatus
from squid.core.builds.history_models import BuildHistorySnapshot
from squid.core.builds.models import Build
from squid.core.notifications.domain import InboxVisibility, RecordSubscriptionFilter, SubscriptionKind
from squid.core.notifications.infrastructure.repository import inbox_predicates
from squid.core.notifications.models import InboxNotification, NotificationSubscription
from squid.core.pagination import Page, PageSelector
from squid.core.persistence.paging import KeysetOrder
from squid.core.submissions.application.reviews import SubmissionDetail, SubmissionSummary
from squid.core.submissions.domain.drafts import DraftStatus
from squid.core.submissions.models.entities import SubmissionDraft
from squid.core.submissions.models.reviews import BuildSubmission
from squid.core.voting.domain import VoteStatus
from squid.core.voting.models import BuildVoteSession, Vote, VoteSession


class PostgresViewerRepository:
    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    @staticmethod
    def _submission_summary(row: BuildSubmission, title: str | None, status: BuildStatus | None) -> SubmissionSummary:
        submitted_title = row.content.get("display_name")
        return SubmissionSummary(
            row.id,
            row.build_id,
            submitted_title if isinstance(submitted_title, str) else title,
            "new_build" if row.edit_vote_id is None else "edit",
            row.decision,
            row.submitted_at.to_stdlib(),
            status == BuildStatus.APPROVED,
            row.decided_revision,
        )

    async def submissions(self, account_id: int, selector: PageSelector, page_size: int) -> Page[SubmissionSummary]:
        session = self._session()
        where = BuildSubmission.submitter_account_id == account_id
        total = await session.scalar(select(func.count()).select_from(BuildSubmission).where(where))
        query = (
            select(BuildSubmission, Build.display_name, Build.submission_status)
            .outerjoin(Build, Build.id == BuildSubmission.build_id)
            .where(where)
        )
        page = await KeysetOrder(
            "my_submissions", BuildSubmission.submitted_at, BuildSubmission.id, descending=True
        ).page(session, query, selector, page_size=page_size, total=total or 0)
        return replace(page, items=tuple(self._submission_summary(*row) for row in page.items))

    async def submission(self, identifier: UUID, owner: int | None) -> SubmissionDetail | None:
        session = self._session()
        query = (
            select(BuildSubmission, Build.display_name, Build.submission_status, Build.revision)
            .outerjoin(Build, Build.id == BuildSubmission.build_id)
            .where(BuildSubmission.id == identifier)
        )
        if owner is not None:
            query = query.where(BuildSubmission.submitter_account_id == owner)
        found = (await session.execute(query)).one_or_none()
        if found is None:
            return None
        row, title, status, revision = found
        vote_id = row.edit_vote_id
        if vote_id is None:
            vote_id = await session.scalar(
                select(BuildVoteSession.vote_session_id)
                .where(BuildVoteSession.build_id == row.build_id, BuildVoteSession.proposal_revision.is_(None))
                .order_by(BuildVoteSession.vote_session_id.desc())
                .limit(1)
            )
        # Retained execution inputs also carry private identity and installation data.
        fields = (
            "category",
            "description",
            "details",
            "capture_dimensions",
            "version_compatibility",
            "tags",
            "completion",
        )
        if row.edit_vote_id is None:
            facts = {key: row.content[key] for key in fields if key in row.content}
        else:
            patch = row.content.get("patch")
            facts = (
                {key: value for key, value in patch.items() if key not in {"credits", "extra_info"}}
                if isinstance(patch, dict)
                else {}
            )
        review_revision = row.base_revision or revision
        if row.decided_revision is not None:
            review_revision = row.base_revision or row.decided_revision - 1
        reviewed = (
            await session.scalar(
                select(BuildHistorySnapshot.snapshot)
                .where(
                    BuildHistorySnapshot.build_id == row.build_id,
                    BuildHistorySnapshot.build_revision == review_revision,
                )
                .order_by(BuildHistorySnapshot.id.desc())
                .limit(1)
            )
            if review_revision is not None
            else None
        )
        reviewed_facts: dict[str, JsonValue] = {}
        if reviewed:
            build = reviewed.get("build")
            if isinstance(build, dict):
                reviewed_facts = {
                    key: build[key]
                    for key in ("display_name", "description", "category", "width", "height", "depth", "version_spec")
                    if build.get(key) is not None
                }
            for key in ("doors", "extenders"):
                if reviewed.get(key):
                    reviewed_facts[key] = reviewed[key]
        return SubmissionDetail(
            self._submission_summary(row, title, status),
            row.base_revision,
            vote_id,
            cast(dict[str, JsonValue], facts),
            review_revision,
            reviewed_facts,
        )

    async def session(self, account_id: int, *, staff: bool) -> tuple[SessionUser, SessionCounts]:
        unread = (
            select(func.count())
            .select_from(InboxNotification)
            .where(
                *inbox_predicates(account_id, InboxVisibility(include_staff=staff)), InboxNotification.read_at.is_(None)
            )
        )
        queue = select(func.count()).select_from(Build).where(Build.submission_status == BuildStatus.PENDING)
        drafts = select(
            func.count(SubmissionDraft.id).label("active"),
            func.count(SubmissionDraft.id)
            .filter(SubmissionDraft.status == DraftStatus.NEEDS_ATTENTION)
            .label("attention"),
        ).where(
            SubmissionDraft.owner_account_id == account_id,
            SubmissionDraft.expires_at > Instant.now(),
            SubmissionDraft.status.in_((DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION)),
        )
        draft_counts = drafts.subquery()
        query = (
            select(
                Account.public_account_id,
                AccountProfile.display_name,
                AccountIdentity,
                unread.scalar_subquery(),
                draft_counts.c.active,
                draft_counts.c.attention,
                queue.scalar_subquery() if staff else literal(None),
            )
            .select_from(Account)
            .join(draft_counts, true())
            .outerjoin(AccountProfile, AccountProfile.account_id == Account.id)
            .outerjoin(
                AccountIdentity,
                (AccountIdentity.id == AccountProfile.avatar_identity_id) & (AccountIdentity.account_id == Account.id),
            )
            .where(Account.id == account_id)
        )
        session = self._session()
        row = (await session.execute(query)).one_or_none()
        if row is None:
            raise AccountNotFoundError(account_id)
        public_id, display_name, avatar, unread_count, active, attention, review = row
        user = SessionUser(public_id, display_name, avatar_url_for(avatar) if avatar else None)
        return user, SessionCounts(unread_count, active, attention, review)

    async def build_state(self, account_id: int, build: Build) -> tuple[dict[UUID, int], ViewerBallot | None]:
        follows = (
            select(NotificationSubscription.subject_id, NotificationSubscription.id)
            .join(Account, Account.public_account_id == NotificationSubscription.subject_id)
            .where(
                NotificationSubscription.account_id == account_id,
                NotificationSubscription.enabled,
                NotificationSubscription.kind == SubscriptionKind.CREATOR,
                Account.id.in_(build.credited_creator_account_ids),
            )
        )
        active = (
            select(BuildVoteSession.vote_session_id)
            .join(VoteSession, VoteSession.id == BuildVoteSession.vote_session_id)
            .where(BuildVoteSession.build_id == build.id, VoteSession.status == VoteStatus.OPEN)
            .order_by(BuildVoteSession.vote_session_id.desc())
            .limit(1)
        )
        ballot = select(Vote.vote_session_id, Vote.option_identifier).where(
            Vote.account_id == account_id, Vote.vote_session_id == active.scalar_subquery()
        )
        session = self._session()
        subscriptions = {key: value for key, value in await session.execute(follows) if key is not None}
        row = (await session.execute(ballot)).one_or_none()
        return subscriptions, ViewerBallot(*row) if row else None

    async def subscription(
        self, account_id: int, kind: SubscriptionKind, target: UUID | RecordSubscriptionFilter
    ) -> int | None:
        query = select(NotificationSubscription.id).where(
            NotificationSubscription.enabled,
            NotificationSubscription.account_id == account_id,
            NotificationSubscription.kind == kind,
            NotificationSubscription.record_filter == target
            if isinstance(target, RecordSubscriptionFilter)
            else NotificationSubscription.subject_id == target,
        )
        session = self._session()
        return await session.scalar(query)

    async def creator(self, account_id: int, public_id: UUID) -> CreatorViewer:
        sub = (
            select(NotificationSubscription.id)
            .where(
                NotificationSubscription.account_id == account_id,
                NotificationSubscription.enabled,
                NotificationSubscription.kind == SubscriptionKind.CREATOR,
                NotificationSubscription.subject_id == public_id,
            )
            .scalar_subquery()
        )
        session = self._session()
        is_self, subscription = (
            await session.execute(select(Account.public_account_id == public_id, sub).where(Account.id == account_id))
        ).one()
        # Public profiles belong to accounts. Claims apply only to unclaimed aliases.
        return CreatorViewer(subscription, is_self, can_claim=False)
