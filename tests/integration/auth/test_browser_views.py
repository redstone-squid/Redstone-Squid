"""Account isolation and queue visibility in the browser read model."""

from dataclasses import replace
from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.infrastructure.repository import AccountRepository
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.auth.application.viewer import SessionCounts, ViewerQueries
from squid.core.errors import NotFoundError
from squid.core.notifications.domain import SubscriptionKind
from squid.core.notifications.models import NotificationSubscription
from squid.core.pagination import FIRST_PAGE
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import ACCOUNT_SELF_READ, BUILD_SUBMISSION_VIEW_PENDING
from squid.core.permissions.errors import PermissionDeniedError
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.submissions.domain.drafts import DraftStatus
from squid.core.submissions.domain.forms import SubmissionOrigin
from squid.core.submissions.models.entities import SubmissionDraft
from squid.core.submissions.models.reviews import BuildSubmission


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
        submission = BuildSubmission(
            build_id=123,
            submitter_account_id=owner.id,
            finalization_job_id=owner.public_account_id,
            content={"description": "Original description", "owner_account_id": owner.id},
        )
        session.add(submission)
        subscription = NotificationSubscription(
            account_id=owner.id, kind=SubscriptionKind.CREATOR, subject_id=other.public_account_id
        )
        session.add(subscription)
        accounts = AccountRepository(lambda: session, "test-pepper")
        assert await accounts.link_browser_identity(owner.id, IdentityProvider.DISCORD, "123")
        assert await accounts.link_browser_identity(owner.id, IdentityProvider.DISCORD, "123")
        assert not await accounts.link_browser_identity(other.id, IdentityProvider.DISCORD, "123")
    views = ViewerQueries(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        PermissionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
    )
    owner_context = ActingContext.direct(UserActor(account_id=owner.id))
    staff = UserActor(account_id=other.id, is_bot_owner=True)
    staff_context = ActingContext.direct(staff)
    user, counts, allowed = await views.session(context=owner_context)
    assert BUILD_SUBMISSION_VIEW_PENDING.name not in allowed
    assert user.id == owner.public_account_id
    assert counts == SessionCounts(0, 1, 1, None)
    _, counts, allowed = await views.session(context=staff_context)
    assert BUILD_SUBMISSION_VIEW_PENDING.name in allowed
    assert counts == SessionCounts(0, 1, 0, 1)
    assert (await views.creator(other.public_account_id, context=owner_context)).subscription_id == subscription.id
    assert (await views.creator(other.public_account_id, context=staff_context)).is_self
    assert await views.subscription(SubscriptionKind.CREATOR, other.public_account_id, context=staff_context) is None

    limited = ActingContext.direct(replace(staff, credential_scopes=frozenset({ACCOUNT_SELF_READ.name})))
    _, counts, allowed = await views.session(context=limited)
    assert counts.review_queue is None
    assert allowed == (ACCOUNT_SELF_READ.name,)
    own_detail = await views.submission(submission.id, context=owner_context)
    assert own_detail.facts == {"description": "Original description"}
    assert not own_detail.can_approve
    assert (await views.submission(submission.id, context=staff_context)).submission == own_detail.submission
    assert (await views.review_queue(FIRST_PAGE, 10, context=staff_context)).items == (own_detail.submission,)
    with pytest.raises(NotFoundError):
        await views.submission(submission.id, context=limited)
    with pytest.raises(PermissionDeniedError):
        await views.review_queue(FIRST_PAGE, 10, context=owner_context)
    denied = ActingContext.direct(replace(staff, credential_scopes=frozenset()))
    with pytest.raises(PermissionDeniedError):
        await views.creator(other.public_account_id, context=denied)
