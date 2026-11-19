"""PostgreSQL tag-definition repository."""

from collections.abc import Callable, Sequence
from typing import override

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.edits.domain import Revision
from squid.persistence.revisions import flush_revision
from squid.tags.application import TagDefinitionRepository
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAlias, TagApplicability, TagAssignment, TagDefinition


class PostgresTagDefinitionRepository(TagDefinitionRepository):
    """Persist definitions and assignments in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

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
        row = TagDefinition(
            stable_key=stable_key,
            display_name=display_name,
            normalized_name=normalized_name,
            query_name=query_name,
            authority=TagAuthority.USER,
            semantic_kind=TagSemanticKind.SHOWCASE,
            restriction_type=None,
            value_type=value_type,
            record_operator=None,
            canonical_unit=None,
            default_display_unit=None,
            numeric_step=None,
            render_template="{name}" if value_type is TagValueType.NONE else "{name}: {value}{unit}",
            default_display_order=0,
            moderation_status=TagModerationStatus.PENDING,
            created_by_account_id=created_by_account_id,
            archived_at=None,
        )
        session = self._session()
        session.add(row)
        await session.flush()
        return row

    @override
    async def pending(self) -> Sequence[TagDefinition]:
        session = self._session()
        rows = (
            await session.scalars(
                select(TagDefinition)
                .where(
                    TagDefinition.moderation_status == TagModerationStatus.PENDING, TagDefinition.withdrawn.is_(False)
                )
                .order_by(TagDefinition.created_at, TagDefinition.id)
            )
        ).all()
        return tuple(row for row in rows)

    @override
    async def get(self, tag_id: int) -> TagDefinition | None:
        session = self._session()
        row = await session.get(TagDefinition, tag_id)
        return None if row is None else row

    @override
    async def reserve_identity(self) -> int:
        identity = await self._session().scalar(text("SELECT nextval(pg_get_serial_sequence('tag_definitions', 'id'))"))
        assert isinstance(identity, int)
        return identity

    async def proposal_for_update(self, tag_id: int) -> TagDefinition | None:
        await self._session().execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"tag:{tag_id}", 0))))
        return await self.get_for_update(tag_id)

    async def create_reserved(self, definition: TagDefinition) -> None:
        self._session().add(definition)
        await self._session().flush()

    async def query_name_available(self, name: str, tag_id: int) -> bool:
        session = self._session()
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"tag-query:{name}", 0))))
        return (
            await session.scalar(
                select(TagDefinition.id).where(TagDefinition.query_name == name, TagDefinition.id != tag_id)
            )
            is None
        )

    async def is_unused(self, tag_id: int) -> bool:
        used = await self._session().scalar(
            select(
                or_(
                    exists().where(TagAssignment.tag_id == tag_id),
                    exists().where(TagAlias.tag_id == tag_id),
                    exists().where(TagApplicability.tag_id == tag_id),
                )
            )
        )
        return not used

    async def get_for_update(self, tag_id: int) -> TagDefinition | None:
        return await self._session().get(TagDefinition, tag_id, with_for_update=True)

    async def flush_definition_revision(self, definition: TagDefinition, expected: Revision) -> Revision:
        return await flush_revision(self._session(), definition, expected)

    @override
    async def approved(self) -> Sequence[TagDefinition]:
        session = self._session()
        rows = (
            await session.scalars(
                select(TagDefinition)
                .where(
                    TagDefinition.moderation_status == TagModerationStatus.APPROVED, TagDefinition.withdrawn.is_(False)
                )
                .order_by(TagDefinition.default_display_order, TagDefinition.display_name, TagDefinition.id)
            )
        ).all()
        return tuple(row for row in rows)

    @override
    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        session = self._session()
        row = await session.get(TagDefinition, tag_id, with_for_update=True)
        if row is None:
            return None
        # Queue the public search refresh with the status change so neither can commit alone.
        row.moderation_status = status
        row.archived_at = Instant.now() if status is TagModerationStatus.ARCHIVED else None
        row.updated_at = Instant.now()
        await session.flush()
        await session.execute(
            text("SELECT upsert_search_projection_request('metadata', :source_key, :action)"),
            {
                "source_key": f"tag:{tag_id}",
                "action": "upsert" if status is TagModerationStatus.APPROVED else "delete",
            },
        )
        return row
