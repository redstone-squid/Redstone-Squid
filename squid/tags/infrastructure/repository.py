"""PostgreSQL tag-definition repository."""

from collections.abc import Sequence
from typing import override

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.infrastructure.mapping import BuildMapper
from squid.tags.application import TagDefinitionRepository
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.tags.infrastructure.models import TagDefinition as SQLTagDefinition


class PostgresTagDefinitionRepository(TagDefinitionRepository):
    """Persist definitions using short independent transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def create_showcase(
        self,
        *,
        stable_key: str,
        display_name: str,
        normalized_name: str,
        value_type: TagValueType,
        query_name: str | None,
        created_by_discord_id: int,
    ) -> TagDefinition:
        row = SQLTagDefinition(
            stable_key=stable_key,
            display_name=display_name,
            normalized_name=normalized_name,
            query_name=query_name,
            authority=TagAuthority.USER,
            semantic_kind=TagSemanticKind.SHOWCASE,
            restriction_type=None,
            value_type=value_type,
            record_operator=None,
            canonical_unit_key=None,
            default_display_unit_key=None,
            numeric_quantum=None,
            render_template="{name}" if value_type is TagValueType.NONE else "{name}: {value}{unit}",
            default_display_order=0,
            moderation_status=TagModerationStatus.PENDING,
            created_by_discord_id=created_by_discord_id,
            archived_at=None,
        )
        async with self._session_factory.begin() as session:
            session.add(row)
            await session.flush()
            return BuildMapper.tag_definition_to_domain(row)

    @override
    async def pending(self) -> Sequence[TagDefinition]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(SQLTagDefinition)
                    .where(SQLTagDefinition.moderation_status == TagModerationStatus.PENDING)
                    .order_by(SQLTagDefinition.created_at, SQLTagDefinition.id)
                )
            ).all()
            return tuple(BuildMapper.tag_definition_to_domain(row) for row in rows)

    @override
    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        async with self._session_factory.begin() as session:
            row = await session.get(SQLTagDefinition, tag_id, with_for_update=True)
            if row is None:
                return None
            row.moderation_status = status
            row.archived_at = Instant.now() if status is TagModerationStatus.ARCHIVED else None
            row.updated_at = Instant.now()
            await session.flush()
            await session.execute(
                text(
                    """
                    INSERT INTO search_projection_queue (resource_kind, source_key, action, enqueued_at)
                    VALUES ('metadata', :source_key, :action, now())
                    ON CONFLICT (resource_kind, source_key) DO UPDATE
                    SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
                        attempts = 0, locked_at = NULL, last_error = NULL
                    """
                ),
                {
                    "source_key": f"tag:{tag_id}",
                    "action": "upsert" if status is TagModerationStatus.APPROVED else "delete",
                },
            )
            return BuildMapper.tag_definition_to_domain(row)
