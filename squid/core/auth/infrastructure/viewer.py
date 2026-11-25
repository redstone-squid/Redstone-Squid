"""Account-scoped read queries for the browser session and detail pages."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.accounts.domain import avatar_url_for
from squid.core.accounts.domain.profiles import CreatorViewer
from squid.core.accounts.errors import AccountNotFoundError
from squid.core.accounts.models import Account, AccountIdentity, AccountProfile
from squid.core.auth.application.viewer import SessionCounts, SessionUser, ViewerBallot
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.notifications.domain import InboxVisibility, RecordSubscriptionFilter, SubscriptionKind
from squid.core.notifications.infrastructure.repository import inbox_predicates
from squid.core.notifications.models import InboxNotification, NotificationSubscription
from squid.core.submissions.domain.drafts import DraftStatus
from squid.core.submissions.models.entities import SubmissionDraft
from squid.core.voting.domain import VoteStatus
from squid.core.voting.models import BuildVoteSession, Vote, VoteSession


class PostgresViewerRepository:
    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

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
                Account.public_creator_id,
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
        creator_id, display_name, avatar, unread_count, active, attention, review = row
        user = SessionUser(account_id, creator_id, display_name, avatar_url_for(avatar) if avatar else None)
        return user, SessionCounts(unread_count, active, attention, review)

    async def build_state(self, account_id: int, build: Build) -> tuple[dict[UUID, int], ViewerBallot | None]:
        follows = (
            select(NotificationSubscription.subject_id, NotificationSubscription.id)
            .join(Account, Account.public_creator_id == NotificationSubscription.subject_id)
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
        ballot = select(Vote.vote_session_id, Vote.option_alias_id).where(
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
            await session.execute(select(Account.public_creator_id == public_id, sub).where(Account.id == account_id))
        ).one()
        # Public profiles belong to accounts. Claims apply only to unclaimed aliases.
        return CreatorViewer(subscription, is_self, can_claim=False)
