"""Account deletion against the migrated schema, including its triggers."""

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import CreditRetention, IdentityProvider, PrivacyNoticeReceipt
from squid.core.accounts.errors import AccountDeletionBlockedError
from squid.core.accounts.infrastructure.account_deletion import delete_account
from squid.core.accounts.infrastructure.repository import AccountRepository
from squid.core.accounts.models import Account, AccountIdentity, AccountProfile
from squid.core.auth.models.sessions import WebSession
from squid.core.builds.domain import BuildStatus
from squid.core.builds.domain.credits import CreditKind
from squid.core.builds.models import BuildCredit, OtherBuild
from squid.core.submissions.domain import SubmissionOrigin
from squid.core.submissions.models.entities import SubmissionDraft
from tests.support.build_edits import build_edit_session
from tests.support.creator_credits import publish_credits


async def _account_with_identity(sessions: async_sessionmaker[AsyncSession], subject: str) -> Account:
    receipt = PrivacyNoticeReceipt.grant_current()
    async with sessions.begin() as session:
        account = Account(privacy_notice_version=receipt.version, privacy_notice_acknowledged_at=receipt.granted_at)
        session.add(account)
        await session.flush()
        session.add_all(
            [
                AccountIdentity(account_id=account.id, provider=IdentityProvider.DISCORD, subject=subject),
                AccountProfile(account_id=account.id, display_name="Red"),
                WebSession(token_hash=b"token", account_id=account.id, expires_at=Instant.now().add(hours=1)),
            ]
        )
    return account


async def test_deletion_keeps_credited_names_but_nothing_that_identifies_the_account(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    account = await _account_with_identity(sessions, "1001")
    linked, matched = await publish_credits(sessions, ["Red", "Redstoner"])
    async with build_edit_session(sessions) as session:
        await session.execute(
            update(BuildCredit)
            .where(BuildCredit.id == linked.id)
            .values(account_id=account.id, account_source="reviewed")
        )
        # Credited through the Discord identity rather than an explicit link.
        await session.execute(
            update(BuildCredit)
            .where(BuildCredit.id == matched.id)
            .values(kind=CreditKind.DISCORD, subject="1001", username="redstoner")
        )

    async with build_edit_session(sessions) as session:
        await delete_account(session, account.id, CreditRetention.KEEP_NAME)

    async with sessions() as session:
        tombstone = await session.get(Account, account.id)
        assert tombstone is not None
        assert tombstone.deleted_at is not None
        assert tombstone.privacy_notice_version is None
        for model in (AccountIdentity, AccountProfile, WebSession):
            assert await session.scalar(select(model).where(model.account_id == account.id)) is None
        credits = (
            await session.execute(
                select(BuildCredit.name, BuildCredit.kind, BuildCredit.subject, BuildCredit.account_id)
                .where(BuildCredit.build_id == linked.build_id)
                .order_by(BuildCredit.position)
            )
        ).all()
        assert [tuple(credit) for credit in credits] == [("Red", "name", None, None), ("Redstoner", "name", None, None)]
        repository = AccountRepository(lambda: session, verification_code_pepper="test-pepper")
        assert await repository.account_id_for_public_id(tombstone.public_account_id) is None


async def test_removing_credits_closes_the_gaps_they_leave(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    account = await _account_with_identity(sessions, "1002")
    _, removed, _ = await publish_credits(sessions, ["Alpha", "Red", "Beta"])
    async with build_edit_session(sessions) as session:
        await session.execute(update(BuildCredit).where(BuildCredit.id == removed.id).values(account_id=account.id))

    async with build_edit_session(sessions) as session:
        await delete_account(session, account.id, CreditRetention.REMOVE)

    async with sessions() as session:
        remaining = (
            await session.execute(
                select(BuildCredit.position, BuildCredit.name)
                .where(BuildCredit.build_id == removed.build_id)
                .order_by(BuildCredit.position)
            )
        ).all()
    assert [tuple(row) for row in remaining] == [(0, "Alpha"), (1, "Beta")]


async def test_a_pending_submission_blocks_deletion_and_erases_nothing(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    account = await _account_with_identity(sessions, "1003")
    async with build_edit_session(sessions) as session:
        session.add(
            OtherBuild(submission_status=BuildStatus.PENDING, submitter_account_id=account.id, ai_generated=False)
        )
        session.add(
            SubmissionDraft(
                owner_account_id=account.id,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
                origin=SubmissionOrigin.WEB,
                expires_at=Instant.now().add(hours=24),
            )
        )

    with pytest.raises(AccountDeletionBlockedError) as blocked:
        async with build_edit_session(sessions) as session:
            await delete_account(session, account.id, CreditRetention.KEEP_NAME)
    assert blocked.value.public_context == {"pending_submissions": 1, "open_votes": 0}

    async with sessions() as session:
        assert await session.scalar(select(Account.deleted_at).where(Account.id == account.id)) is None
        assert await session.scalar(select(SubmissionDraft.id).where(SubmissionDraft.owner_account_id == account.id))
        assert await session.scalar(select(AccountIdentity.id).where(AccountIdentity.account_id == account.id))
