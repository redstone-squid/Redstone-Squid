"""PostgreSQL tag-definition repository."""

from collections.abc import Sequence
from decimal import Decimal
from typing import override

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.infrastructure.mapping import BuildMapper
from squid.builds.infrastructure.models import Build
from squid.tags.application import TagDefinitionRepository
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValue,
    TagValueType,
)
from squid.tags.infrastructure.models import BuildTagAssignment
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
        created_by_account_id: int,
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
            numeric_step=None,
            render_template="{name}" if value_type is TagValueType.NONE else "{name}: {value}{unit}",
            default_display_order=0,
            moderation_status=TagModerationStatus.PENDING,
            created_by_account_id=created_by_account_id,
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
    async def get(self, tag_id: int) -> TagDefinition | None:
        async with self._session_factory() as session:
            row = await session.get(SQLTagDefinition, tag_id)
            return None if row is None else BuildMapper.tag_definition_to_domain(row)

    @override
    async def approved(self) -> Sequence[TagDefinition]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(SQLTagDefinition)
                    .where(SQLTagDefinition.moderation_status == TagModerationStatus.APPROVED)
                    .order_by(
                        SQLTagDefinition.default_display_order, SQLTagDefinition.display_name, SQLTagDefinition.id
                    )
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
                    INSERT INTO search_projection_queue
                        (resource_kind, source_key, action, enqueued_at, available_at)
                    VALUES ('metadata', :source_key, :action, now(), now())
                    ON CONFLICT (resource_kind, source_key) DO UPDATE
                    SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
                        available_at = EXCLUDED.available_at, attempts = 0,
                        locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL
                    """
                ),
                {
                    "source_key": f"tag:{tag_id}",
                    "action": "upsert" if status is TagModerationStatus.APPROVED else "delete",
                },
            )
            return BuildMapper.tag_definition_to_domain(row)

    @override
    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool:
        async with self._session_factory.begin() as session:
            # Compares the build's owning account to the actor's, rather than joining
            # `account_identities` to compare two snowflakes -- which also means an
            # account with no Discord identity can tag its own build.
            owned_build_id = await session.scalar(
                select(Build.id).where(Build.id == build_id, Build.submitter_account_id == actor_account_id)
            )
            if owned_build_id is None:
                return False
            definition = await session.get(SQLTagDefinition, tag_id)
            if definition is None:
                return False
            # `value_type` is a bare `Text` column with no TypeDecorator, so a row read
            # back from the database carries a `str` where the annotation promises the
            # enum. `_split_value` compares with `is`, so every branch fell through and
            # every assignment raised. Coerced here rather than in `_split_value` so the
            # helper keeps a single, honest input type.
            value_type = TagValueType(definition.value_type)
            numeric_value, text_value, boolean_value = _split_value(value_type, value)
            statement = insert(BuildTagAssignment).values(
                build_id=build_id,
                tag_id=tag_id,
                value_type=definition.value_type,
                numeric_value=numeric_value,
                text_value=text_value,
                boolean_value=boolean_value,
                provenance="submitted",
                created_by_account_id=actor_account_id,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[BuildTagAssignment.build_id, BuildTagAssignment.tag_id],
                    set_={
                        "numeric_value": statement.excluded.numeric_value,
                        "text_value": statement.excluded.text_value,
                        "boolean_value": statement.excluded.boolean_value,
                        # An `Instant`, not a stdlib datetime: the column's `InstantUTC`
                        # decorator does the conversion, and pre-converting made its
                        # `process_bind_param` raise on every upsert.
                        "updated_at": Instant.now(),
                    },
                )
            )
            return True


def _split_value(
    value_type: TagValueType,
    value: TagValue,
) -> tuple[Decimal | None, str | None, bool | None]:
    if value_type is TagValueType.NONE and value is None:
        return None, None, None
    if value_type is TagValueType.NUMERIC and isinstance(value, Decimal):
        return value, None, None
    if value_type is TagValueType.TEXT and isinstance(value, str):
        return None, value, None
    if value_type is TagValueType.BOOLEAN and isinstance(value, bool):
        return None, None, value
    msg = f"invalid {value_type.value} tag value"
    raise ValueError(msg)
