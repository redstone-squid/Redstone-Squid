"""Showcase tag ownership, which is now an account comparison rather than a snowflake one."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import AccountIdentity as AccountIdentityValue
from squid.accounts.infrastructure.models import Account
from squid.accounts.infrastructure.models import AccountIdentity as AccountIdentityModel
from squid.builds.domain import Status
from squid.builds.infrastructure.models import Build
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.infrastructure.models import BuildTagAssignment, TagDefinition
from squid.tags.infrastructure.repository import PostgresTagDefinitionRepository


@pytest.fixture
def repository(migrated_session_factory: async_sessionmaker[AsyncSession]) -> PostgresTagDefinitionRepository:
    return PostgresTagDefinitionRepository(migrated_session_factory)


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
        build = Build(
            submitter_account_id=submitter_account_id,
            submission_status=Status.PENDING,
            category=None,
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
            canonical_unit_key=None,
            default_display_unit_key=None,
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
    repository: PostgresTagDefinitionRepository,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Impossible before: ownership joined `account_identities` to compare two snowflakes."""
    account_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322445)
    build_id = await _build(migrated_session_factory, submitter_account_id=account_id)
    tag_id = await _approved_tag(migrated_session_factory, created_by_account_id=account_id)

    assigned = await repository.assign_showcase(
        build_id=build_id, tag_id=tag_id, value=None, actor_account_id=account_id
    )

    assert assigned is True
    async with migrated_session_factory() as session:
        stored = await session.scalar(select(BuildTagAssignment).where(BuildTagAssignment.build_id == build_id))
        assert stored is not None
        assert stored.created_by_account_id == account_id


async def test_an_account_cannot_tag_someone_elses_build(
    repository: PostgresTagDefinitionRepository,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322445)
    stranger_id = await _account(migrated_session_factory, bedrock_xuid=2535465049322446)
    build_id = await _build(migrated_session_factory, submitter_account_id=owner_id)
    tag_id = await _approved_tag(migrated_session_factory, created_by_account_id=owner_id)

    assigned = await repository.assign_showcase(
        build_id=build_id, tag_id=tag_id, value=None, actor_account_id=stranger_id
    )

    assert assigned is False
    async with migrated_session_factory() as session:
        assert await session.scalar(select(BuildTagAssignment)) is None
