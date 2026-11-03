"""Showcase tag ownership, which is now an account comparison rather than a snowflake one."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.accounts.models import AccountIdentity as AccountIdentityModel
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.builds.domain import Status
from squid.builds.models import OtherBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition


@pytest.fixture
async def uow(migrated_session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
        yield work


async def _account(session_factory: async_sessionmaker[AsyncSession], *, bedrock_xuid: int) -> int:
    """An account with a Bedrock identity and deliberately no Discord one."""
    identity = AccountIdentityValue.bedrock(bedrock_xuid)
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(AccountIdentityModel(account_id=account.id, provider=identity.provider, subject=identity.subject))
        return account.id


async def _build(session_factory: async_sessionmaker[AsyncSession], *, submitter_account_id: int) -> int:
    async with session_factory.begin() as session:
        build = OtherBuild(
            submitter_account_id=submitter_account_id,
            submission_status=Status.PENDING,
            record_category=None,
            width=1,
            height=2,
            depth=3,
            completion_time=None,
        )
        session.add(build)
        await session.flush()
        return build.id


async def _approved_tag(session_factory: async_sessionmaker[AsyncSession], *, created_by_account_id: int) -> int:
    async with session_factory.begin() as session:
        definition = TagDefinition(
            stable_key="user_00000000000000000000000000000001",
            display_name="Showcase",
            normalized_name="showcase",
            query_name=None,
            authority=TagAuthority.USER,
            semantic_kind=TagSemanticKind.SHOWCASE,
            restriction_type=None,
            value_type=TagValueType.NONE,
            record_operator=None,
            canonical_unit=None,
            default_display_unit=None,
            numeric_step=None,
            render_template="{name}",
            default_display_order=0,
            moderation_status=TagModerationStatus.APPROVED,
            created_by_account_id=created_by_account_id,
            archived_at=None,
        )
        session.add(definition)
        await session.flush()
        return definition.id


async def test_an_account_without_discord_can_tag_its_own_build(
    uow: SqlAlchemyUnitOfWork,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Impossible before: ownership joined `account_identities` to compare two snowflakes."""
    account_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322445)
    build_id = await _build(migrated_session_factory, submitter_account_id=account_id)
    tag_id = await _approved_tag(migrated_session_factory, created_by_account_id=account_id)

    assigned = await uow.tags.assign_showcase(build_id=build_id, tag_id=tag_id, value=None, actor_account_id=account_id)

    await uow.commit()
    assert assigned is True
    async with migrated_session_factory() as session:
        stored = await session.scalar(select(TagAssignment).where(TagAssignment.build_id == build_id))
        assert stored is not None
        assert stored.created_by_account_id == account_id


async def test_an_account_cannot_tag_someone_elses_build(
    uow: SqlAlchemyUnitOfWork,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322445)
    stranger_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322446)
    build_id = await _build(migrated_session_factory, submitter_account_id=owner_id)
    tag_id = await _approved_tag(migrated_session_factory, created_by_account_id=owner_id)

    assigned = await uow.tags.assign_showcase(
        build_id=build_id, tag_id=tag_id, value=None, actor_account_id=stranger_id
    )

    await uow.commit()
    assert assigned is False
    async with migrated_session_factory() as session:
        assert await session.scalar(select(TagAssignment)) is None


@pytest.mark.parametrize("commit", [False, True])
async def test_moderation_and_projection_request_share_a_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    commit: bool,
) -> None:
    account_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322445)
    tag_id = await _approved_tag(migrated_session_factory, created_by_account_id=account_id)
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
        updated = await work.tags.set_status(tag_id, TagModerationStatus.ARCHIVED)
        assert updated is not None
        assert updated.moderation_status is TagModerationStatus.ARCHIVED
        if commit:
            await work.commit()
    async with migrated_session_factory() as session:
        stored = await session.get(TagDefinition, tag_id)
        assert stored is not None
        assert stored.moderation_status is (TagModerationStatus.ARCHIVED if commit else TagModerationStatus.APPROVED)
        queued = await session.scalar(
            text("SELECT action FROM search_projection_queue WHERE resource_kind = 'metadata' AND source_key = :key"),
            {"key": f"tag:{tag_id}"},
        )
        assert queued == ("delete" if commit else "upsert")
