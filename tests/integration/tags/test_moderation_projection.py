"""Tag moderation and its search projection request commit or roll back together."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.accounts.models import AccountIdentity as AccountIdentityModel
from squid.core.accounts.models import AccountIdentity as AccountIdentityValue
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from tests.support.build_edits import build_edit_session


async def _account(session_factory: async_sessionmaker[AsyncSession], *, bedrock_xuid: int) -> int:
    """An account with a Bedrock identity and deliberately no Discord one."""
    identity = AccountIdentityValue.bedrock(bedrock_xuid)
    async with build_edit_session(session_factory) as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(AccountIdentityModel(account_id=account.id, provider=identity.provider, subject=identity.subject))
        return account.id


async def _approved_tag(session_factory: async_sessionmaker[AsyncSession], *, created_by_account_id: int) -> int:
    async with build_edit_session(session_factory) as session:
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
