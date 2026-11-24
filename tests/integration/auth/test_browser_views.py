"""Account isolation and queue visibility in the browser read model."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.infrastructure.repository import AccountRepository
from squid.core.accounts.models import Account
from squid.core.auth.infrastructure.viewer import SessionCounts, ViewerQueries
from squid.core.notifications.domain import SubscriptionKind
from squid.core.notifications.models import NotificationSubscription
from squid.core.submissions.domain.drafts import DraftStatus
from squid.core.submissions.domain.forms import SubmissionOrigin
from squid.core.submissions.models.entities import SubmissionDraft


async def test_browser_reads_and_links_are_scoped_to_the_account(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        owner, other = Account(), Account()
        session.add_all([owner, other])
        await session.flush()
        for account, status in [(owner, DraftStatus.NEEDS_ATTENTION), (other, DraftStatus.EDITING)]:
            session.add(
                SubmissionDraft(
                    owner_account_id=account.id,
                    schema_id="door",
                    schema_revision=1,
                    category="door",
                    status=status,
                    origin=SubmissionOrigin.WEB,
                    expires_at=Instant.now().add(hours=1),
                )
            )
        subscription = NotificationSubscription(
            account_id=owner.id, kind=SubscriptionKind.CREATOR, subject_id=other.public_creator_id
        )
        session.add(subscription)
        accounts = AccountRepository(lambda: session, "test-pepper")
        assert await accounts.link_browser_identity(owner.id, IdentityProvider.DISCORD, "123")
        assert await accounts.link_browser_identity(owner.id, IdentityProvider.DISCORD, "123")
        assert not await accounts.link_browser_identity(other.id, IdentityProvider.DISCORD, "123")
    views = ViewerQueries(migrated_session_factory)
    user, counts = await views.session(owner.id, staff=False)
    assert user.creator_id == owner.public_creator_id
    assert counts == SessionCounts(0, 1, 1, None)
    _, counts = await views.session(other.id, staff=True)
    assert counts == SessionCounts(0, 1, 0, 0)
    assert (await views.creator(owner.id, other.public_creator_id)).subscription_id == subscription.id
    assert (await views.creator(other.id, other.public_creator_id)).is_self
    assert await views.subscription(other.id, SubscriptionKind.CREATOR, other.public_creator_id) is None
